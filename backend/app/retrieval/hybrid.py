"""Hybrid retrieval: dense (BGE+Qdrant) + sparse(bm25), each fetching max(top_k, Candidate_depth)
candidates, fused with reciprocal rank fusion(k=60), cut to top_k"""


import argparse
import json
from pathlib import Path

from ingestion.pipeline.common import require

from .artifacts import BM25_DIR, PROCESSED_DIR, CorpusArtifacts, search_request
from .dense import DenseRetriever
from .fusion import RRF_K, reciprocal_rank_fusion
from .sparse import SparseRetriever

HYBRID_VERSION= "hybrid-rrf-1"
CANDIDATE_DEPTH = 20

RESULT_FIELDS= (
    "citation_id",
    "act",
    "kind",
    "header",
    "source_pages",
    "json_pointer",
    "text",
)

class HybridRetriever:
    #one query, two retrievers, one fused list of citable passages

    def __init__(self, dense, sparse):
        require(
            dense.artifacts.chunks_sha256 == sparse.artifacts.chunks_sha256,
            "the dense and sparse retrievers index differnt corpora.",
        )
        self.dense = dense
        self.sparse= sparse

    @classmethod
    def standard(
        cls,
        *,
        processed_dir=PROCESSED_DIR,
        index_dir=BM25_DIR,
        url=None,
        device="cpu",
        allow_download=False,
    ):
        artifacts = CorpusArtifacts(processed_dir)
        dense= DenseRetriever(
            processed_dir=processed_dir,
            url=url,
            device=device,
            allow_download=allow_download,
            artifacts=artifacts,
        )
        sparse = SparseRetriever(
            processed_dir=processed_dir,
            index_dir=index_dir,
            artifacts=artifacts,      
        )
        return cls(dense, sparse)
    
    def search(self, query, top_k=20, *, act=None):
        query, act = search_request(query, top_k, act)
        if not query:
            return self._response(query, act, top_k, [], [], [])
        # Always fetch more candidates, then cut after fusion
        candidate_k = max(top_k, CANDIDATE_DEPTH)
        dense_hits = self.dense.search(query, candidate_k, act=act)
        sparse_hits = self.sparse.search(query, candidate_k, act=act)
        fused = reciprocal_rank_fusion([dense_hits, sparse_hits], top_k)
        return self._response(query, act, top_k, fused, dense_hits, sparse_hits)

    def check(self):
        """Run both retrievers' integrity checks and confirm one shared corpus."""
        dense_report = self.dense.check()
        sparse_report = self.sparse.check()
        return {
            "status": "passed",
            "source": "hybrid",
            "retriever": HYBRID_VERSION,
            "fusion": {"method": "rrf", "k": RRF_K},
            "dense": dense_report,
            "sparse": sparse_report,
            "verified": "Both sides checked and indexed the same chunk set.",
        }
    
    def close(self):
        self.dense.close()

    def _response(self, query, act, top_k, fused, dense_hits, sparse_hits):
        def _ranks(hits):
            out={}
            for i, hit in enumerate(hits,start=1):
                cid=hit["chunk_id"]
                if cid not in out:
                    out[cid]=i
            return out
        
        dense_rank= _ranks(dense_hits)
        sparse_rank = _ranks(sparse_hits)

        results = []
        for rank, entry in enumerate(fused, start=1):
            payload = entry["payload"]
            cid = entry["chunk_id"]

            result = {
                "rank": rank,
                "chunk_id": cid,
                "rrf_score": entry["rrf_score"],
                "sources": entry["sources"],
                "ranks": {
                    "dense": dense_rank.get(cid),
                    "sparse": sparse_rank.get(cid),
                },
            }

            for field in RESULT_FIELDS:
                result[field] = payload[field]

            results.append(result)
        return {
            "status": "ok",
            "retriever": HYBRID_VERSION,
            "query": query,
            "act": act,
            "top_k": top_k,
            "candidate_depth": max(top_k, CANDIDATE_DEPTH),
            "chunks_sha256": self.dense.artifacts.chunks_sha256,
            "counts": {
                "dense": len(dense_hits),
                "sparse": len(sparse_hits),
                "fused": len(results),
            },
            "results": results,
        }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--check-only", action="store_true")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--index-dir", type=Path, default=BM25_DIR)
    parser.add_argument(
        "--url", default=None, help="Qdrant URL; defaults to the QDRANT_URL environment variable."
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--allow-download", action="store_true")
    args = parser.parse_args()
    retriever = HybridRetriever.standard(
        processed_dir=args.processed_dir,
        index_dir=args.index_dir,
        url=args.url,
        device=args.device,
        allow_download=args.allow_download,
    )
    try:
        result = (
            retriever.check()
            if args.check_only
            else retriever.search(args.query, args.top_k, act=args.act)
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    finally:
        retriever.close()


if __name__ == "__main__":
    main()