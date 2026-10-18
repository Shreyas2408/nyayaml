
from __future__ import annotations

import argparse
import json
import re

# --- Constants ---------------------------------------------------------------

COMPRESSOR_VERSION = "extractive-minilm-1"
SENTENCE_THRESHOLD = 0.0

# MiniLM ms-marco logits live roughly in [-10, +10]. 0.0 is the WP5b freeze;
# retune ONLY after printing one live run's sentence scores (see smoke step).

MINILM_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
MINILM_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"

# Conservative sentence split for legal text (advisory finding 5):
# break ONLY where [.!?] is followed by whitespace AND the next token starts
# with an uppercase letter, a digit, or an opening quote. This deliberately
# does NOT break:
#   "sub-sec. (1) of section 132."  -> period + space + "(" fails lookahead
#   "Explanation.—"                 -> em-dash is not in [.!?]
#   PDF line wraps                  -> no bare-\n split; Gazette chunks wrap
#                                      mid-sentence, so \n is not a boundary.
# Under-splitting (one slightly long span) is always safer than over-splitting
# (orphaning an exception or proviso from the operative sentence it qualifies).
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\u201c\"'])")

_ENCODER_HOOK_MSG = (
    "WP5b could not obtain the shared MiniLM from the reranker.\n"
    "Fix (one line in backend/app/retrieval/reranker.py):\n"
    "    def get_encoder(self):\n"
    "        return self.model  # whatever attribute holds the CrossEncoder\n"
    "Do NOT construct a second CrossEncoder anywhere."
)
_NO_RERANKER_MSG = (
    "Compressor needs a reranker (WP5a) or an injected encoder=. Pass reranker=, "
    "or make backend/app/retrieval/reranker.py constructible as "
    "Reranker(allow_download=...)."
)


# --- Pure helpers ------------------------------------------------------------

def split_sentences(passage: str) -> list[dict]:
    """Return [{text, char_start, char_end}] in document order.

    Offsets index into the ORIGINAL passage string. A piece that cannot be
    located by running find() is skipped — offsets are measured, never guessed.
    """
    if not passage or not str(passage).strip():
        return []
    spans: list[dict] = []
    cursor = 0
    for part in SENTENCE_SPLIT.split(passage):
        piece = part.strip()
        if not piece:
            continue
        start = passage.find(piece, cursor)
        if start < 0:
            continue
        end = start + len(piece)
        spans.append({"text": piece, "char_start": start, "char_end": end})
        cursor = end
    return spans


def _passage_and_field(hit: dict) -> tuple:
    
    if hit.get("embedding_text"):
        return hit["embedding_text"], "embedding_text"
    if hit.get("text"):
        return hit["text"], "text"
    return "", None


def _encoder_from_reranker(reranker):
    """Reuse the CrossEncoder WP5a already loaded. Never construct another."""
    if reranker is None:
        raise RuntimeError(_NO_RERANKER_MSG)
    getter = getattr(reranker, "get_encoder", None)
    if callable(getter):
        enc = getter()
        if enc is not None and hasattr(enc, "predict"):
            return enc
    for attr in ("model", "encoder", "_model", "_encoder"):
        obj = getattr(reranker, attr, None)
        if obj is not None and hasattr(obj, "predict"):
            return obj
    for name in ("_load_model", "load_model", "_get_model"):
        loader = getattr(reranker, name, None)
        if callable(loader):
            enc = loader()
            if enc is not None and hasattr(enc, "predict"):
                return enc
    raise RuntimeError(_ENCODER_HOOK_MSG)


def _build_reranker(allow_download):
    from .reranker import Reranker

    return Reranker(allow_download=allow_download)


# --- Compressor ---------------------------------------------------------------

class Compressor:
    """Filter reranked hits down to query-relevant verbatim sentences."""

    def __init__(self, reranker=None, encoder=None, *, allow_download=False,
                 threshold=SENTENCE_THRESHOLD):
        self.reranker = reranker
        self._encoder = encoder
        self.allow_download = allow_download
        self.threshold = threshold

    # -- encoder plumbing ----------------------------------------------------
    def _get_encoder(self):
        if self._encoder is None:
            if self.reranker is None:
                self.reranker = _build_reranker(self.allow_download)
            self._encoder = _encoder_from_reranker(self.reranker)
        return self._encoder

    def _peek_encoder(self):
        """Already-loaded encoder only. Never downloads, never lazy-loads."""
        if self._encoder is not None:
            return self._encoder
        if self.reranker is not None:
            for attr in ("model", "encoder", "_model", "_encoder"):
                obj = getattr(self.reranker, attr, None)
                if obj is not None and hasattr(obj, "predict"):
                    return obj
        return None

    # -- core: this is the WP5b product ---------------------------------------
    def compress_hit(self, query: str, hit: dict) -> dict:
        out = dict(hit)  # copy — the reranker's list is never mutated
        passage, field = _passage_and_field(hit)

        if not str(query or "").strip() or not passage.strip():
            out.update(passage_field=field, compression_mode="skipped",
                       low_confidence=False, spans=[], compressed_text="")
            return out

        sentences = split_sentences(passage)
        if not sentences:
            out.update(passage_field=field, compression_mode="skipped",
                       low_confidence=False, spans=[], compressed_text="")
            return out

        pairs = [(str(query).strip(), s["text"]) for s in sentences]
        scores = self._get_encoder().predict(pairs)

        # Fail closed on encoder contract violation (advisory finding 4):
        # a short score list must never be zip()-truncated in silence.
        if len(scores) != len(sentences):
            raise RuntimeError(
                f"encoder returned {len(scores)} scores for "
                f"{len(sentences)} sentences — refusing to truncate"
            )

        kept: list[dict] = []
        best_i, best_score = 0, float("-inf")
        for i, (span, raw) in enumerate(zip(sentences, scores)):
            score = float(raw)
            if score > best_score:
                best_score, best_i = score, i
            s, e, t = span["char_start"], span["char_end"], span["text"]
            # Verbatim gate: an offset that does not round-trip is a bug,
            # not a span.
            if score >= self.threshold and passage[s:e] == t:
                kept.append({"text": t, "char_start": s, "char_end": e,
                             "score": score})

        mode = "threshold"
        if not kept:
            # WP5b safety: a reranked hit always yields >= 1 span. Keep the
            # single best sentence, but FLAG it (advisory finding 6): the
            # downstream prompt builder must know this is a degraded,
            # low-confidence compression and may prefer the full passage.
            # Never silently drop an exception or proviso.
            span = sentences[best_i]
            kept = [{"text": span["text"], "char_start": span["char_start"],
                     "char_end": span["char_end"],
                     "score": float(scores[best_i])}]
            mode = "fallback_best"

        kept.sort(key=lambda s: s["char_start"])  # document order, not score order
        out.update(passage_field=field, compression_mode=mode,
                   low_confidence=(mode == "fallback_best"), spans=kept,
                   compressed_text=" ".join(s["text"] for s in kept))
        return out

    def compress(self, query: str, hits: list) -> list:
        if not hits:
            return []
        if not str(query or "").strip():
            return []  # empty query: predict is never called
        return [self.compress_hit(query, hit) for hit in hits]

    # -- CLI orchestration ----------------------------------------------------
    

    def search(self, query, top_k=3, *, act=None):
        empty = {
            "status": "ok",
            "compressor": COMPRESSOR_VERSION,
            "query": str(query or "").strip(),
            "act": act,
            "top_k": top_k,
            "threshold": self.threshold,
            "model": MINILM_MODEL,
            "revision": MINILM_REVISION,
            "device": "cpu",
            "counts": {"reranked": 0, "compressed": 0},
            "results": [],
        }
        if not str(query or "").strip():
            return empty

        if self.reranker is None:
            self.reranker = _build_reranker(self.allow_download)

        search_fn = getattr(self.reranker, "search", None)
        if not callable(search_fn):
            raise TypeError(
                "Reranker.search is missing. Add it (Step 1) so it returns "
                "the same JSON as the reranker CLI."
            )
        reranked = search_fn(query, top_k=top_k, act=act)

        if not isinstance(reranked, dict) or "results" not in reranked:
            raise TypeError("Reranker.search() must return a dict with 'results'")

        rows = reranked["results"]
        compressed = self.compress(query, rows)
        counts = dict(reranked.get("counts") or {})
        counts["reranked"] = len(rows)
        counts["compressed"] = len(compressed)
        out = dict(reranked)
        out.update(
            status="ok",
            compressor=COMPRESSOR_VERSION,
            threshold=self.threshold,
            counts=counts,
            results=compressed,
        )
        return out

    # -- honest check (advisory finding 7) -------------------------------------
    def check(self) -> dict:
        info = {
            "status": "declared",  # wiring declared, model not yet exercised
            "verified": False,
            "compressor": COMPRESSOR_VERSION,
            "threshold": self.threshold,
            "device": "cpu",
            "model": MINILM_MODEL,
            "revision": MINILM_REVISION,
        }
        enc = self._peek_encoder()  # already-loaded only; never downloads
        if enc is None:
            # Honest cold-start status. Run one live query (or inject the
            # encoder) and check() will report "passed" with a probe score.
            return info
        try:
            probe = enc.predict([("wp5b probe", "wp5b probe")])
            if len(probe) != 1:
                raise RuntimeError(
                    f"predict returned {len(probe)} scores for 1 pair")
            info["status"] = "passed"
            info["verified"] = True
            info["probe_score"] = float(probe[0])
        except (TypeError, ValueError, RuntimeError,OSError) as exc:
            info["status"] = "failed"
            info["error"] = str(exc)
        return info

    def close(self) -> None:
        if self.reranker is not None and hasattr(self.reranker, "close"):
            self.reranker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--check-only", action="store_true")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args()

    compressor = Compressor(allow_download=args.allow_download)
    try:
        result = (compressor.check() if args.check_only else
                  compressor.search(args.query, top_k=args.top_k, act=args.act))
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    finally:
        compressor.close()


if __name__ == "__main__":
    main()