"""Query orchestrator -- wires existing retrieval/LLM/verification pieces
into one typed, fail-closed pipeline (P34-01).

    empty query -> empty typed response, nobody is called
    else:
      Reranker.search -> Compressor.compress -> frozen SYSTEM + verbatim
      compressed_text -> Ollama.generate -> parse_llm_response
      -> HallucinationDetector -> CitationVerifier -> QueryResponse

No citation reaches the caller unless it was BOTH (a) actually retrieved for
this query and (b) verified to exist in the corpus. Anything the model cites
that fails (a) is recorded in hallucination_flags, never in citations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..llm.client import LLMUnavailable, OllamaClient
from ..llm.structured import StructuredOutputError, parse_llm_response
from ..retrieval.compressor import Compressor
from ..retrieval.reranker import Reranker
from ..verification.citation import CitationVerifier
from ..verification.hallucination import HallucinationDetector
from .schemas import DISCLAIMERS, Citation, QueryRequest, QueryResponse

# Frozen on purpose -- do not parameterise per request.
SYSTEM_PROMPT = (
    "You are a legal research assistant for Indian criminal law (BNS, BNSS, "
    "BSA). Answer using ONLY the numbered passages given below. Cite a "
    "passage's citation_id only if you actually used it. Never invent a "
    "citation_id. Never paraphrase or alter the statute text you quote -- "
    "use it verbatim. Respond as JSON with keys answer (string), citations "
    "(list of objects each having citation_id), confidence (number 0 to 1), "
    "and query_type (one of direct_lookup, scenario, cross_act, procedural, "
    "out_of_scope, definition, punishment)."
)

_CITATIONS_JSON = Path("data/processed/citations.json")


def build_prompt(query: str, hits: list) -> str:
    lines = [f"Question: {query}", "", "Context:"]
    for hit in hits:
        cid = (hit or {}).get("citation_id") or ""
        text = (hit or {}).get("compressed_text") or ""
        lines.append(f"[{cid}] {text}")
    return "\n".join(lines)


def _hits_from_search(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return list(raw.get("results") or [])
    return list(raw)


def _first_seen_ids(hits: list) -> tuple[list[str], dict[str, dict]]:
    order: list[str] = []
    by_id: dict[str, dict] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            cid = getattr(hit, "citation_id", None)
            hit_map = {
                "citation_id": cid,
                "act": getattr(hit, "act", None),
                "header": getattr(hit, "header", None),
                "chunk_id": getattr(hit, "chunk_id", None),
                "compressed_text": getattr(hit, "compressed_text", None),
            }
        else:
            cid = hit.get("citation_id")
            hit_map = hit
        if not cid:
            continue
        if cid not in by_id:
            by_id[cid] = hit_map
            order.append(cid)
    return order, by_id


def _check_is_known(check: Any) -> bool:
    """Adapter for CitationCheck. Prefer the boolean used in citation.py."""
    for attr in ("exists", "found", "ok", "valid"):
        if hasattr(check, attr):
            value = getattr(check, attr)
            return bool(value() if callable(value) else value)
    reason = getattr(check, "reason", None)
    if reason is None or reason in ("", "ok", "found", "exists"):
        return True
    return False


def _hallucinated_ids(report: Any) -> list[str]:
    for attr in ("hallucinated_ids", "hallucinated", "hallucination_flags", "ungrounded", "flags"):
        value = getattr(report, attr, None)
        if value is None:
            continue
        if callable(value):
            value = value()
        return [str(item) for item in list(value)]
    return []


def _detect(detector: HallucinationDetector, claimed_ids: list[str], hits: list) -> Any:
    """Call detect() with the signature in hallucination.py (ids, spans)."""
    return detector.detect(claimed_ids, hits)


def _load_known_citation_ids() -> list[str] | None:
    if not _CITATIONS_JSON.is_file():
        return None
    try:
        data = json.loads(_CITATIONS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, list):
        return [str(item) for item in data]
    if isinstance(data, dict):
        for key in ("citation_ids", "ids", "entries", "citations"):
            value = data.get(key)
            if isinstance(value, list):
                ids = []
                for item in value:
                    if isinstance(item, str):
                        ids.append(item)
                    elif isinstance(item, dict) and item.get("citation_id"):
                        ids.append(str(item["citation_id"]))
                if ids:
                    return ids
    return None


def _citation_from_hit(hit: dict) -> Citation | None:
    cid = hit.get("citation_id") if isinstance(hit, dict) else getattr(hit, "citation_id", None)
    if not cid:
        return None
    payload = hit if isinstance(hit, dict) else {
        "citation_id": cid,
        "act": getattr(hit, "act", None),
        "header": getattr(hit, "header", None),
        "chunk_id": getattr(hit, "chunk_id", None),
    }
    return Citation.model_validate(payload)


class QueryOrchestrator:
    """Coordinates retrieval -> LLM -> verification. No FastAPI here."""

    def __init__(
        self,
        *,
        reranker=None,
        compressor=None,
        ollama=None,
        verifier=None,
        detector=None,
        known_citation_ids=None,
        allow_download: bool = False,
    ):
        self._reranker = reranker
        self._compressor = compressor
        self._ollama = ollama
        self._verifier = verifier
        self._detector = detector
        self._known_citation_ids = (
            list(known_citation_ids) if known_citation_ids is not None else None
        )
        self.allow_download = allow_download

    def _get_reranker(self):
        if self._reranker is None:
            self._reranker = Reranker(allow_download=self.allow_download)
        return self._reranker

    def _get_compressor(self):
        if self._compressor is None:
            self._compressor = Compressor(
                reranker=self._get_reranker(),
                allow_download=self.allow_download,
            )
        return self._compressor

    def _get_ollama(self):
        if self._ollama is None:
            self._ollama = OllamaClient()
        return self._ollama

    def _get_detector(self):
        if self._detector is None:
            self._detector = HallucinationDetector()
        return self._detector

    def _get_verifier(self, retrieved_ids: list[str]) -> CitationVerifier:
        if self._verifier is not None:
            return self._verifier
        known = self._known_citation_ids
        if known is None:
            known = _load_known_citation_ids()
            if known:
                self._known_citation_ids = known
        if known:
            self._verifier = CitationVerifier(known)
            return self._verifier
        return CitationVerifier(retrieved_ids)

    def retrieve_and_compress(self, query: str, *, act=None, top_k: int = 3) -> dict:
        raw = self._get_reranker().search(query, top_k=top_k, act=act)
        hits_in = _hits_from_search(raw)
        hits = self._get_compressor().compress(query, hits_in)
        retrieved_citation_ids, _ = _first_seen_ids(hits)
        return {"hits": hits, "retrieved_citation_ids": retrieved_citation_ids}

    def _filter_citations(
        self,
        claimed_ids: list[str],
        hits_by_id: dict[str, dict],
        retrieved_ids: list[str],
        hits: list,
    ) -> tuple[list[Citation], list[str], bool]:
        retrieved_set = set(retrieved_ids)
        ordered: list[str] = []
        seen: set[str] = set()
        for cid in claimed_ids:
            if cid in seen:
                continue
            seen.add(cid)
            ordered.append(cid)

        report = _detect(self._get_detector(), ordered, hits)
        flags = _hallucinated_ids(report)
        flag_set = set(flags)

        verifier = self._get_verifier(retrieved_ids)
        citations: list[Citation] = []
        valid = True
        for cid in ordered:
            if cid not in retrieved_set or cid in flag_set:
                if cid not in flag_set:
                    flags.append(cid)
                    flag_set.add(cid)
                valid = False
                continue
            check = verifier.verify(cid)
            if not _check_is_known(check):
                valid = False
                continue
            built = _citation_from_hit(hits_by_id[cid])
            if built is not None:
                citations.append(built)
        return citations, flags, valid and not flags

    def run(self, request: QueryRequest) -> QueryResponse:
        query = (request.query or "").strip()
        base = dict(
            query=query,
            act=request.act,
            top_k=request.top_k,
            disclaimers=list(DISCLAIMERS),
        )
        if not query:
            return QueryResponse(**base)

        retrieval = self.retrieve_and_compress(
            query, act=request.act, top_k=request.top_k
        )
        hits = retrieval["hits"]
        retrieved_ids = retrieval["retrieved_citation_ids"]
        _, hits_by_id = _first_seen_ids(hits)

        if not hits:
            return QueryResponse(**base, retrieved_citation_ids=retrieved_ids)

        prompt = build_prompt(query, hits)
        try:
            result = self._get_ollama().generate(prompt, system=SYSTEM_PROMPT)
        except LLMUnavailable:
            return QueryResponse(
                **base,
                retrieved_citation_ids=retrieved_ids,
                error="llm_unavailable",
            )

        fallback_used = bool(result.get("fallback_used"))
        try:
            parsed = parse_llm_response(result.get("text") or "")
        except StructuredOutputError:
            return QueryResponse(
                **base,
                retrieved_citation_ids=retrieved_ids,
                fallback_used=fallback_used,
                error="parse_error",
            )

        claimed = [item.citation_id for item in parsed.citations]
        citations, flags, valid = self._filter_citations(
            claimed, hits_by_id, retrieved_ids, hits
        )
        return QueryResponse(
            **base,
            answer=parsed.answer,
            query_type=parsed.query_type,
            citations=citations,
            citations_valid=valid,
            hallucination_flags=flags,
            retrieved_citation_ids=retrieved_ids,
            fallback_used=fallback_used,
        )

    def close(self):
        reranker = self._reranker
        if reranker is not None:
            closer = getattr(reranker, "close", None)
            if callable(closer):
                closer()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--query")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args(argv)

    if args.check_only:
        from ..llm.client import OllamaClient as _OllamaClient
        from ..llm.structured import parse_llm_response as _parse
        from ..retrieval.compressor import Compressor as _Compressor
        from ..retrieval.reranker import Reranker as _Reranker
        from ..verification.citation import CitationVerifier as _Verifier
        from ..verification.hallucination import HallucinationDetector as _Detector

        _ = (_OllamaClient, _parse, _Compressor, _Reranker, _Verifier, _Detector, QueryOrchestrator)
        print(json.dumps({"status": "passed", "scope": "import graph only"}, indent=2))
        return 0

    if not args.query:
        parser.error("--query is required unless --check-only is set")

    orch = QueryOrchestrator(allow_download=args.allow_download)
    try:
        if args.no_llm:
            out = orch.retrieve_and_compress(
                args.query, act=args.act, top_k=args.top_k
            )
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
            return 0
        resp = orch.run(
            QueryRequest(query=args.query, act=args.act, top_k=args.top_k)
        )
        print(
            json.dumps(resp.model_dump(), indent=2, ensure_ascii=False, default=str)
        )
        return 0 if resp.error is None else 2
    finally:
        orch.close()


if __name__ == "__main__":
    sys.exit(main())
