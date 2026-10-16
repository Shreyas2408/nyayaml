import argparse
import json
from pathlib import Path

from ingestion.pipeline.bm25_builder import load_index
from ingestion.pipeline.common import require

from .artifacts import (
    BM25_DIR,
    PROCESSED_DIR,
    CorpusArtifacts,
    search_hit,
    search_request,
)


class SparseRetriever:

    def __init__(self, *, artifacts=None, processed_dir=PROCESSED_DIR, index_dir=BM25_DIR):
        self.artifacts=(
            artifacts if artifacts is not None else CorpusArtifacts(processed_dir)
        )
        self.index = load_index(Path(index_dir), qdrant_report=self.artifacts.loader)

        require(
            self.index.chunks == self.artifacts.chunks,
            "BM25 contains different chunks records. Rebuild it from the current chunks.jsonl",
        )

    def check(self):
        return {
            "status": "passed",
            "source": "sparse",
            "count": len(self.index.chunks),
            "counts_by_act": self.artifacts.counts_by_act,
            "chunks_sha256": self.artifacts.chunks_sha256,
            "dense_collection": self.index.metadata["dense_collection"],
            "verified": "Saved index hash, build compatibility and every source chunk record.",
            "live_qdrant_check": False,
        }
    
    def search(self, query, top_k=20, *, act=None):
        query, act = search_request(query, top_k, act)
        if not query:
            return []
        # load_index reuses the builder's tokenizer; do not add a BGE prefix here.
        hits = self.index.search(query, top_k=top_k, act=act)
        return [search_hit(hit["chunk"], hit["score"], "sparse") for hit in hits]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--check-only", action="store_true")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--index-dir", type=Path, default=BM25_DIR)
    args = parser.parse_args()
    retriever = SparseRetriever(
        processed_dir=args.processed_dir, index_dir=args.index_dir
    )
    result = (
        retriever.check()
        if args.check_only
        else retriever.search(
            args.query,
            args.top_k,
            act=args.act,
        )
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()