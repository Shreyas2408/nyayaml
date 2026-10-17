"""Cross-encoder reranker for retrieval results and CLI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2"
MODEL_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
FUSION_CUT = 40


class Reranker:
    """Rerank fused retrieval hits with a CPU cross-encoder."""

    def __init__(self, encoder: Any | None = None, *, allow_download: bool = False):
        self._encoder = encoder
        self.allow_download = allow_download

    def _load_model(self):
        if self._encoder is None:
            from sentence_transformers import CrossEncoder

            self._encoder = CrossEncoder(
                MODEL_NAME,
                revision=MODEL_REVISION,
                device="cpu",
                local_files_only=not self.allow_download,
            )
        return self._encoder

    def rerank(
        self,
        query: str,
        fused_hits: list[dict[str, Any]],
        top_k: int = 10,
        *,
        fusion_cut: int = FUSION_CUT,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if fusion_cut < 1:
            raise ValueError("fusion_cut must be a positive integer")
        if not query or not query.strip() or not fused_hits:
            return []

        pool = fused_hits[:fusion_cut]
        candidates = [
            hit
            for hit in pool
            if (hit.get("embedding_text") or hit.get("text") or "").strip()
        ]
        if not candidates:
            return []

        pairs = [
            (query.strip(), hit.get("embedding_text") or hit.get("text") or "")
            for hit in candidates
        ]
        scores = self._load_model().predict(pairs)

        ranked = []
        for hit, score in zip(candidates, scores):
            row = dict(hit)
            row["rerank_score"] = float(score)
            ranked.append(row)

        ranked.sort(key=lambda h: h["rerank_score"], reverse=True)
        results = ranked[:top_k]
        for i, hit in enumerate(results, start=1):
            hit["rank"] = i
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--check-only", action="store_true")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--fusion-cut", type=int, default=FUSION_CUT)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument("--url", default=None)
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args()

    reranker = Reranker(allow_download=args.allow_download)

    if args.check_only:
        reranker._load_model()
        print(
            json.dumps(
                {
                    "status": "passed",
                    "model": MODEL_NAME,
                    "revision": MODEL_REVISION,
                    "device": "cpu",
                    "fusion_cut": FUSION_CUT,
                },
                indent=2,
            )
        )
        return

    from .artifacts import BM25_DIR, PROCESSED_DIR
    from .hybrid import HybridRetriever

    retriever = HybridRetriever.standard(
        processed_dir=args.processed_dir or PROCESSED_DIR,
        index_dir=args.index_dir or BM25_DIR,
        url=args.url,
        device="cpu",
        allow_download=args.allow_download,
    )
    try:
        hybrid_out = retriever.search(
            args.query,
            top_k=args.top_k,
            act=args.act,
            fusion_cut=args.fusion_cut,
        )
        results = reranker.rerank(
            args.query,
            hybrid_out["results"],
            top_k=args.top_k,
            fusion_cut=args.fusion_cut,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "retriever": "reranker-minilm-1",
                    "query": hybrid_out.get("query", args.query),
                    "act": hybrid_out.get("act", args.act),
                    "top_k": args.top_k,
                    "model": MODEL_NAME,
                    "revision": MODEL_REVISION,
                    "device": "cpu",
                    "candidate_depth": hybrid_out.get("candidate_depth"),
                    "fusion_cut": args.fusion_cut,
                    "chunks_sha256": hybrid_out.get("chunks_sha256"),
                    "counts": {
                        **hybrid_out.get("counts", {}),
                        "reranked": len(results),
                    },
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    finally:
        retriever.close()


if __name__ == "__main__":
    main()