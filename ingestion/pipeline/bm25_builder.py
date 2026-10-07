"""Build and serialize BM25 index. Implemented in Phase 1."""

import argparse
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from importlib.metadata import version
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .common import data_hash, file_hash, read_json, require
from .config import OUTPUT_DIR, PROJECT

BUILDER_VERSION="bm25-builder-1"
TOKENIZER_VERSION="legal-words-and-references-1"
PACKAGE_VERSION = "0.2.2"
PARAMETERS= {"k1": 1.5, "b":0.75, "epsilon": 0.25}

INDEX_DIR = PROJECT / "data" / "bm25_index"
SOURCE_NAME = "Bns_Bnss_Bsa_2023_final.json"

WORD= re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
REFERENCE = re.compile(r"\b\d+[a-z]?(?:\s*\(\s*[0-9a-z]+\s*\))+")
REFERENCE_PART = re.compile(r"\([^()]+\)")

def tokenize(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = normalized.replace("’", "'").replace("‘", "'")
    tokens = WORD.findall(normalized)

    #keep ordinary words and numbers , plus references such as 359(1)(a)
    for match in REFERENCE.finditer(normalized):
        reference = re.sub(r"\s+", "", match.group())
        prefix = reference.split("(", 1)[0]
        for part in REFERENCE_PART.findall(reference):
            prefix += part
            tokens.append(prefix)

    return tokens

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)

def check_dense_report(report, chunks_hash, source_hash, counts):
    #compare the saved Qdrant report
    require(report["status"]=="passed","the qdrant load has not passed")
    require(
        report["chunks_sha256"] == chunks_hash, "Qdrant and BM25 would use different chunks.rerun the loader.",
    )
    require(
        report["canonical_data_sha256"] == source_hash,
        "Qdrant and BM25 would use different source JSON.",
    )
    require(report["points"] == sum(counts.values()), "Qdrant point count differs.")
    require(report["counts_by_act"] == counts, "Qdrant per-act counts differ.")
    require(
        report["embedding_dim"] == 768 and report["distance"] == "cosine",
        "The Qdrant report is not for the expected 768-dimensional index.",
    )

def prepare(processed_dir):
    #validate actual files before creating any index output
    require(
        version("rank-bm25") == PACKAGE_VERSION,
        f"Install rank-bm25 =={PACKAGE_VERSION} for this index version.",
    )
    chunks_path = processed_dir / "chunks.jsonl"
    chunking= read_json(processed_dir / "chunk_report.json")
    validation = read_json(processed_dir / "validation_report.json")
    dense= read_json(processed_dir / "qdrant_load_report.json")
    source_hash = data_hash(read_json(processed_dir/ SOURCE_NAME))
    chunks_hash = file_hash(chunks_path)

    require(
        validation["blocking_defects"] == [],
        "resolve the source validation defects before building BM25",
    )
    require(
        validation["reference_match_excluding_row_ids"] is True,
        "The source JSON has not passed the reference comparison.",
    )
    require(
        source_hash
        == validation["canonical_data_sha256"]
        == chunking["canonical_data_sha256"],
        "the source json , validation report and chunk report does not matched",
    )
    require(
        chunks_hash == chunking["chunks_sha256"],
        "chunks.jsonl has changed since the chunker ran.",
    )
    with chunks_path.open(encoding="utf-8") as handle:
        chunks = [json.loads(line) for line in handle if line.strip()]
    require(len(chunks) > 0,"There are no chunks to index")
    require(len(chunks) == chunking["chunks"], "the chunk report count differs")

    ids = [chunk["chunk_id"] for chunk in chunks]
    require(
        all(isinstance(item, str) and item for item in ids),
        "Each chunk needs a nonempty string chunk_id.",
    )
    require(len(set(ids)) == len(ids), "Duplicate chunk_id values found.")

    tokens = []
    for chunk in chunks:
        require(
            chunk["act"] in {"BNS", "BNSS", "BSA"},
            f"Unknown act in {chunk['chunk_id']}.",
        )
        for key in ("text", "embedding_text", "citation_id", "json_pointer"):
            require(
                isinstance(chunk[key], str) and chunk[key].strip(),
                f"Missing {key} in {chunk['chunk_id']}.",
            )
        terms = tokenize(chunk["embedding_text"])
        require(bool(terms), f"No searchable terms in {chunk['chunk_id']}.")
        tokens.append(terms)

    counts = dict(sorted(Counter(chunk["act"] for chunk in chunks).items()))
    check_dense_report(dense, chunks_hash, source_hash, counts)
    metadata = {
        "builder_version": BUILDER_VERSION,
        "tokenizer_version": TOKENIZER_VERSION,
        "rank_bm25_version": PACKAGE_VERSION,
        "algorithm": "BM25Okapi",
        "parameters": PARAMETERS,
        "indexed_field": "embedding_text",
        "count": len(chunks),
        "counts_by_act": counts,
        "chunks_sha256": chunks_hash,
        "canonical_data_sha256": source_hash,
        "dense_build_id": dense["build_id"],
        "dense_alias": dense["alias"],
        "dense_collection": dense["collection"],
        "embeddings_sha256": dense["embeddings_sha256"],
    }
    return {"metadata": metadata, "chunks": chunks, "tokens": tokens}

class BM25Index:
    #keeping one instance in memory and reusing it for subsequent searches
    def __init__(self, bundle):
        self.metadata = bundle["metadata"]
        self.chunks = bundle["chunks"]
        self.bm25 = BM25Okapi(bundle["tokens"], **self.metadata["parameters"])
        self.postings = defaultdict(set)
        for index, terms in enumerate(bundle["tokens"]):
            for term in set(terms):
                self.postings[term].add(index)
    def search(self, query, top_k=10, *, act=None, section=None, kind=None):
        require(top_k > 0, "top_k must be greater than zero.")
        act = act.upper() if act is not None else None
        require(act is None or act in {"BNS", "BNSS", "BSA"}, "Unknown act filter.")
        require(section is None or act is not None, "Supply an act with section.")
        terms = tokenize(query)
        candidates = set()
        for term in terms:
            candidates.update(self.postings.get(term, ()))

        eligible = []
        for index in candidates:
            chunk = self.chunks[index]
            if act is not None and chunk["act"] != act:
                continue
            if section is not None and str(
                chunk.get("citation_path", {}).get("section_number")
            ) != str(section):
                continue
            if kind is not None and chunk["kind"] != kind:
                continue
            eligible.append(index)
        if not eligible:
            return []

        scores = self.bm25.get_scores(terms)
        require(np.isfinite(scores).all(), "BM25 produced non-finite scores.")
        eligible.sort(
            key=lambda index: (-float(scores[index]), self.chunks[index]["chunk_id"])
        )
        return [
            {"score": float(scores[index]), "chunk": self.chunks[index]}
            for index in eligible[:top_k]
        ]

    def check_dense_report(self, report):
        """Call at backend startup with the current saved loader report."""
        check_dense_report(
            report,
            self.metadata["chunks_sha256"],
            self.metadata["canonical_data_sha256"],
            self.metadata["counts_by_act"],
        )
        for dense_key, saved_key in (
            ("build_id", "dense_build_id"),
            ("alias", "dense_alias"),
            ("collection", "dense_collection"),
            ("embeddings_sha256", "embeddings_sha256"),
        ):
            require(
                report[dense_key] == self.metadata[saved_key],
                f"The saved BM25 index refers to a different {dense_key}.",
            )

def load_index(index_dir=INDEX_DIR, *, qdrant_report=None):
    """Verify the saved index, then reconstruct its BM25 statistics once."""
    pointer = read_json(Path(index_dir) / "current.json")
    identity = pointer["build_id"]
    require(
        re.fullmatch(r"[0-9a-f]{16}", identity) is not None, "Invalid BM25 build ID."
    )
    path = Path(index_dir) / "builds" / identity / "index.json"
    require(
        file_hash(path) == pointer["index_sha256"],
        "The saved BM25 index has changed. Rebuild it.",
    )
    bundle = read_json(path)
    metadata = bundle["metadata"]
    require(data_hash(metadata)[:16] == identity, "BM25 metadata does not match.")
    require(
        metadata["builder_version"] == BUILDER_VERSION
        and metadata["tokenizer_version"] == TOKENIZER_VERSION
        and metadata["rank_bm25_version"] == version("rank-bm25") == PACKAGE_VERSION,
        "BM25 code or dependency version changed. Rebuild the index.",
    )
    require(
        len(bundle["chunks"]) == len(bundle["tokens"]) == metadata["count"],
        "The saved BM25 index has inconsistent lengths.",
    )
    index = BM25Index(bundle)
    if qdrant_report is not None:
        index.check_dense_report(qdrant_report)
    return index


def build(bundle, index_dir):
    """Write a versioned index and publish current.json only after checking it."""
    metadata = bundle["metadata"]
    identity = data_hash(metadata)[:16]
    path = index_dir / "builds" / identity / "index.json"
    original = BM25Index(bundle)
    atomic_json(path, bundle)
    restored_bundle = read_json(path)
    require(restored_bundle == bundle, "Saved index differs from its input.")
    restored = BM25Index(restored_bundle)

    # Check serialization with real corpus terms. Retrieval relevance is
    # evaluated separately with a reviewed set of legal questions.
    for position in sorted({0, len(bundle["chunks"]) // 2, len(bundle["chunks"]) - 1}):
        query = bundle["chunks"][position]["header"]
        require(
            original.search(query) == restored.search(query),
            "BM25 rankings changed after saving and loading.",
        )

    lengths = [len(terms) for terms in bundle["tokens"]]
    report = {
        **metadata,
        "status": "passed",
        "build_id": identity,
        "index_sha256": file_hash(path),
        "vocabulary_size": len(original.postings),
        "average_document_terms": round(sum(lengths) / len(lengths), 2),
        "maximum_document_terms": max(lengths),
        "verified": "Input hashes, IDs, counts, saved corpus and ranking round-trip.",
        "dense_check": "Saved Qdrant load report only; no live server request.",
    }
    atomic_json(path.parent / "bm25_report.json", report)
    atomic_json(
        index_dir / "current.json",
        {
            "build_id": identity,
            "index_sha256": report["index_sha256"],
        },
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--index-dir", type=Path, default=INDEX_DIR)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--query", help="Search the saved index without rebuilding it.")
    parser.add_argument("--act", choices=("BNS", "BNSS", "BSA"))
    parser.add_argument("--section", help="Section number; also supply --act.")
    parser.add_argument("--kind", help="For example: section, table_row or schedule.")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    if args.query is not None:
        dense = read_json(args.processed_dir / "qdrant_load_report.json")
        index = load_index(args.index_dir, qdrant_report=dense)
        result = index.search(
            args.query, args.top_k, act=args.act, section=args.section, kind=args.kind
        )
    else:
        require(
            args.act is None and args.section is None and args.kind is None,
            "Filters are for --query. Every build indexes all input chunks.",
        )
        bundle = prepare(args.processed_dir)
        if args.check_only:
            result = {"status": "input_check_passed", **bundle["metadata"]}
        else:
            result = build(bundle, args.index_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()