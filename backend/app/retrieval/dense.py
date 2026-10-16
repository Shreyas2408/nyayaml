import argparse
import json
import os
from pathlib import Path
from threading import Lock

import numpy as np
from ingestion.pipeline.common import file_hash, require
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue

from .artifacts import (
    ALIAS,
    EMBEDDING_DIM,
    MODEL_NAME,
    MODEL_REVISION,
    PROCESSED_DIR,
    CorpusArtifacts,
    point_id,
    search_hit,
    search_request,
)

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MAX_TOKENS = 512

class DenseRetriever:
    def __init__(
        self,
        *,
        artifacts=None,
        processed_dir=PROCESSED_DIR,
        url=None,
        device="cpu",
        allow_download=False,
        client=None,
        encoder=None,
    ):
        self.artifacts = (
            artifacts if artifacts is not None else CorpusArtifacts(processed_dir)
        )
        self.collection = self.artifacts.loader["collection"]
        self.alias = ALIAS
        self.device = device
        self.allow_download = allow_download
        self._encoder = encoder  # Optional injection for tests; normally leave unset.
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else QdrantClient(
                url=url or os.environ.get("QDRANT_URL", "http://localhost:6333"),
                api_key=os.environ.get("QDRANT_API_KEY"),
                timeout=60,
            )
        )
        self._lock = Lock()
        self._checked = False
    
    def _check_alias(self):
        aliases = {
            item.alias_name: item.collection_name
            for item in self.client.get_aliases().aliases
        }
        require(
            aliases.get(self.alias) == self.collection,
            "Qdrant alias and local build report differ. Rebuild/reload both retrievers.",
        )
    
    def check(self):
        """Verify the live collection once. This command does not load BGE."""
        with self._lock:
            self._check_alias()
            if not self._checked:
                config = self.client.get_collection(
                    self.collection
                ).config.params.vectors
                require(
                    config.size == EMBEDDING_DIM and config.distance == Distance.COSINE,
                    "Qdrant collection has the wrong vector configuration.",
                )
                require(
                    self.client.count(self.collection, exact=True).count
                    == len(self.artifacts.chunks),
                    "Qdrant point count differs from the source chunks.",
                )
                path = self.artifacts.processed_dir / "embeddings.npy"
                require(
                    file_hash(path) == self.artifacts.embedding["embeddings_sha256"],
                    "embeddings.npy differs from the embedding report.",
                )
                vectors = np.load(path, allow_pickle=False)
                require(
                    vectors.dtype == np.float32
                    and vectors.shape == (len(self.artifacts.chunks), EMBEDDING_DIM),
                    "Embeddings have the wrong dtype or shape.",
                )
                require(
                    np.isfinite(vectors).all(), "Embeddings contain NaN or infinity."
                )
                norms = np.linalg.norm(vectors, axis=1)
                require(
                    np.allclose(norms, 1.0, rtol=0.0, atol=1e-5),
                    "Stored embedding inputs are not unit-normalized.",
                )

                for start in range(0, len(self.artifacts.chunks), 128):
                    batch = self.artifacts.chunks[start : start + 128]
                    expected = {
                        point_id(chunk["chunk_id"]): start + offset
                        for offset, chunk in enumerate(batch)
                    }
                    records = self.client.retrieve(
                        self.collection,
                        ids=list(expected),
                        with_payload=True,
                        with_vectors=True,
                    )
                    require(
                        len(records) == len(expected)
                        and {str(record.id) for record in records} == set(expected),
                        "Qdrant is missing one or more expected points.",
                    )
                    for record in records:
                        index = expected[str(record.id)]
                        chunk = self.artifacts.check_payload(record.payload)
                        require(
                            chunk["chunk_id"]
                            == self.artifacts.chunks[index]["chunk_id"],
                            "A Qdrant point ID belongs to a different chunk.",
                        )
                        require(
                            np.allclose(
                                np.asarray(record.vector, dtype=np.float32),
                                vectors[index] / norms[index],
                                rtol=1e-5,
                                atol=1e-6,
                            ),
                            f"Qdrant vector differs for {chunk['chunk_id']}.",
                        )
                self._check_alias()
                self._checked = True
        return {
             "status": "passed",
            "source": "dense",
            "alias": self.alias,
            "collection": self.collection,
            "count": len(self.artifacts.chunks),
            "counts_by_act": self.artifacts.counts_by_act,
            "chunks_sha256": self.artifacts.chunks_sha256,
            "verified": "Live alias, count, every point ID, payload and vector.",
        }
    def _embed(self, query):
        # Keep a single model in memory and avoid concurrent first-time loads.
        with self._lock:
            if self._encoder is None:
                from sentence_transformers import SentenceTransformer

                self._encoder = SentenceTransformer(
                    MODEL_NAME,
                    revision=MODEL_REVISION,
                    device=self.device,
                    local_files_only=not self.allow_download,
                    trust_remote_code=False,
                )
                self._encoder.max_seq_length = MAX_TOKENS
            text = QUERY_PREFIX + query
            tokens = self._encoder.tokenizer(
                text, add_special_tokens=True, truncation=False
            )
            require(
                len(tokens["input_ids"]) <= MAX_TOKENS,
                "Query exceeds 512 BGE tokens including the prefix. Shorten it; no text was truncated.",
            )
            vector = np.asarray(
                self._encoder.encode(
                    [text],
                    prompt="",
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )[0],
                dtype=np.float32,
            )
        require(
            vector.shape == (EMBEDDING_DIM,) and np.isfinite(vector).all(),
            "BGE returned an invalid query vector.",
        )
        require(
            np.isclose(np.linalg.norm(vector), 1.0, rtol=0.0, atol=1e-5),
            "BGE returned an unnormalized query vector.",
        )
        return vector

    def search(self, query, top_k=20, *, act=None):
        query, act = search_request(query, top_k, act)
        if not query:
            return []
        self.check()
        vector = self._embed(query)
        query_filter = None
        if act is not None:
            query_filter = Filter(
                must=[FieldCondition(key="act", match=MatchValue(value=act))]
            )
        response = self.client.query_points(
            collection_name=self.alias,
            query=vector.tolist(),
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
            with_vectors=False,
        )
        # Discard the response if a new build replaced the alias during the request.
        self._check_alias()
        hits = []
        for point in response.points:
            chunk = self.artifacts.check_payload(point.payload)
            require(
                str(point.id) == point_id(chunk["chunk_id"]),
                "Unexpected Qdrant point ID.",
            )
            require(
                act is None or chunk["act"] == act, "Qdrant returned a different act."
            )
            hits.append(search_hit(chunk, point.score, "dense"))
        return sorted(hits, key=lambda hit: (-hit["score"], hit["chunk_id"]))

    def close(self):
        if self._owns_client:
            self.client.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--query")
    mode.add_argument("--check-only", action="store_true")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument(
        "--url", default=os.environ.get("QDRANT_URL", "http://localhost:6333")
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow fetching missing model files; default uses the local HF cache.",
    )
    args = parser.parse_args()
    retriever = DenseRetriever(
        processed_dir=args.processed_dir,
        url=args.url,
        device=args.device,
        allow_download=args.allow_download,
    )
    try:
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
    finally:
        retriever.close()

if __name__ == "__main__":
    main()