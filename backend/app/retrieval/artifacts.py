import json
import math
import uuid
from collections import Counter
from copy import deepcopy
from pathlib import Path

from ingestion.pipeline.common import data_hash, file_hash, read_json, require

PROJECT = Path(__file__).resolve().parents[3]
PROCESSED_DIR = PROJECT / "data" / "processed"
BM25_DIR = PROJECT / "data" / "bm25_index"

MODEL_NAME = "BAAI/bge-base-en-v1.5"
MODEL_REVISION = "a5beb1e3e68b9ab74eb54cfd186867f64f240e1a"
EMBEDDING_DIM = 768
ALIAS = "nyayaml_knowledge"
POINT_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/Shreyas2408/nyayaml"
)

def point_id(chunk_id):
    # Must remain identical to the namespace used by qdrant_loader.py.
    return str(uuid.uuid5(POINT_NAMESPACE, chunk_id))


def search_request(query, top_k, act):
    require(isinstance(query, str), "Query must be a string.")
    require(type(top_k) is int and top_k > 0, "top_k must be a positive integer.")
    act = act.strip().upper() if act is not None else None
    require(
        act is None or act in {"BNS", "BNSS", "BSA"}, "Use BNS, BNSS or BSA for act."
    )
    return query.strip(), act

def search_hit(chunk, score, source):
    require(math.isfinite(float(score)), "The search score is not finite.")
    return {
        "chunk_id": chunk["chunk_id"],
        "score": float(score),
        "source": source,
        "payload": deepcopy(chunk),
    }

class CorpusArtifacts:
    def __init__(self, processed_dir=PROCESSED_DIR):
        self.processed_dir = Path(processed_dir)
        chunking = read_json(self.processed_dir / "chunk_report.json")
        validation = read_json(self.processed_dir / "validation_report.json")
        source = read_json(self.processed_dir / "Bns_Bnss_Bsa_2023_final.json")
        self.loader = read_json(self.processed_dir / "qdrant_load_report.json")
        self.embedding = read_json(self.processed_dir / "embedding_report.json")

        require(
            validation["blocking_defects"] == [],
            "Source validation has blocking defects.",
        )
        require(
            validation["reference_match_excluding_row_ids"] is True,
            "The source JSON has not passed its reference check.",
        )
        source_hash = data_hash(source)
        require(
            source_hash
            == validation["canonical_data_sha256"]
            == chunking["canonical_data_sha256"]
            == self.loader["canonical_data_sha256"],
            "Source JSON and index reports describe different data.",
        )

        path = self.processed_dir / "chunks.jsonl"
        self.chunks_sha256 = file_hash(path)
        require(
            self.chunks_sha256
            == chunking["chunks_sha256"]
            == self.embedding["chunks_sha256"]
            == self.loader["chunks_sha256"],
            "Chunk file and index reports do not match. Rebuild the affected index.",
        )
        require(
            self.embedding["embeddings_sha256"] == self.loader["embeddings_sha256"],
            "Embedding and Qdrant reports describe different vectors.",
        )
        with path.open(encoding="utf-8") as handle:
            self.chunks = [json.loads(line) for line in handle if line.strip()]
        self.by_id = {chunk["chunk_id"]: chunk for chunk in self.chunks}
        require(
            len(self.by_id) == len(self.chunks) > 0, "Missing or duplicate chunk IDs."
        )
        require(
            len(self.chunks)
            == chunking["chunks"]
            == self.embedding["count"]
            == self.loader["points"],
            "Chunk counts differ between the files and reports.",
        )
        self.counts_by_act = dict(Counter(chunk["act"] for chunk in self.chunks))
        require(
            set(self.counts_by_act) <= {"BNS", "BNSS", "BSA"}, "Unknown act in corpus."
        )
        require(
            self.counts_by_act == self.loader["counts_by_act"], "Per-act counts differ."
        )
        require(self.loader["status"] == "passed", "Qdrant load report has not passed.")
        require(self.loader["alias"] == ALIAS, f"Expected the {ALIAS} alias.")
        require(
            self.loader["embedding_dim"]
            == self.embedding["embedding_dim"]
            == EMBEDDING_DIM,
            "Expected 768-dimensional embeddings.",
        )
        require(
            self.loader["distance"] == "cosine"
            and self.embedding["normalized"] is True,
            "Expected normalized embeddings and cosine distance.",
        )
        require(
            self.embedding["model"] == MODEL_NAME
            and self.embedding["model_revision"] == MODEL_REVISION,
            "The query model must match the model and revision used for ingestion.",
        )
        for chunk in self.chunks:
            for field in (
                "chunk_id",
                "act",
                "citation_id",
                "json_pointer",
                "text",
                "embedding_text",
            ):
                require(
                    isinstance(chunk[field], str) and bool(chunk[field].strip()),
                    f"Missing {field} in a source chunk.",
                )

    def check_payload(self, payload):
        require(isinstance(payload, dict), "Qdrant returned no payload.")
        chunk_id = payload.get("chunk_id")
        require(
            chunk_id in self.by_id, f"Qdrant returned an unknown chunk: {chunk_id}."
        )
        require(
            "text" in payload,
            "Qdrant payload is missing text. Set DERIVED_FIELDS = {'embedding_text'} "
            "in qdrant_loader.py and rerun the loader.",
        )
        expected = self.by_id[chunk_id]
        for key, value in expected.items():
            if key != "embedding_text":
                require(
                    key in payload and payload[key] == value,
                    f"Qdrant payload differs at {chunk_id}: {key}.",
                )
        if "embedding_text" in payload:
            require(
                payload["embedding_text"] == expected["embedding_text"],
                f"Stored embedding_text differs for {chunk_id}.",
            )
        return expected