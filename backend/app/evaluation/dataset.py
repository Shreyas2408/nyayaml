"""Offline P34-02 snapshot questions, validation, and deterministic lookups.

Gold IDs are retrieval targets, not findings of legal correctness. Candidate
labels require legal review; this dataset makes no current-law or case-law claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS_PATH = ROOT / "data/benchmarks/questions_v2.json"
MANIFEST_PATH = ROOT / "data/benchmarks/MANIFEST.json"
CORPUS_PATH = ROOT / "reference/Bns_Bnss_Bsa_2023_final.json"
CITATIONS_PATH = ROOT / "data/processed/citations.json"
CHUNKS_PATH = ROOT / "data/processed/chunks.jsonl"
ACTS = ("BNS", "BNSS", "BSA")
CATEGORIES = ("direct_lookup", "procedural", "cross_act", "scenario")
PROVENANCES = ("gold_retrieval", "corpus_section", "generated_candidate")
GENERATOR_ID = "nyayaml-direct-lookup-v1"
DEFAULT_SEED = 3402
STRIDE = 37
PER_ACT = 6
GOLD_SEED_IDS = (
    "BNSS-2023-8047fe1de609-S359",
    "BNS-2023-ff92dcc72778-S103",
    "BSA-2023-993882ad0ae7-S63",
)
CORPUS_SHA256 = "970fdfe0c7851137114ed42227310e290d5d085350d522f8ce0f05e0c81db0b7"
CHUNKS_SHA256 = "e6565a67c49412a2c0c31fe655bfeee41634c8ea9f5968d17d0a12fad7b6d514"
REQUIRED_FIELDS = {
    "id", "query", "act", "category", "gold_citation_ids", "provenance",
    "needs_legal_review",
}
OPTIONAL_FIELDS = {"json_pointer", "notes"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_citations(path: Path = CITATIONS_PATH) -> dict[str, dict[str, Any]]:
    entries = _read_json(path)["entries"]
    by_id = {entry["citation_id"]: entry for entry in entries}
    _require(len(by_id) == len(entries) > 0, "Missing or duplicate citation entries")
    return by_id


def _resolve_pointer(document: Any, pointer: str) -> Any:
    node = document
    for part in pointer.lstrip("/").split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def validate_questions(
    questions: list[dict[str, Any]], citations: dict[str, dict[str, Any]]
) -> None:
    """Reject malformed rows, duplicate questions, and unsupported gold IDs."""
    _require(isinstance(questions, list) and bool(questions), "Expected question rows")
    ids: set[str] = set()
    texts: set[str] = set()
    for row in questions:
        _require(isinstance(row, dict), "Question must be an object")
        _require(REQUIRED_FIELDS <= row.keys(), "Missing required question fields")
        _require(row.keys() <= REQUIRED_FIELDS | OPTIONAL_FIELDS, "Unknown question fields")
        for field in ("id", "query", "provenance", "category"):
            _require(
                isinstance(row[field], str) and bool(row[field].strip()),
                f"Invalid {field}",
            )
        _require(row["id"] == row["id"].strip(), "Question ID has outer whitespace")
        normalized = " ".join(unicodedata.normalize("NFKC", row["query"]).casefold().split())
        _require(row["id"] not in ids, f"Duplicate question ID: {row['id']}")
        _require(normalized not in texts, f"Duplicate query: {row['query']}")
        ids.add(row["id"])
        texts.add(normalized)
        _require(row["category"] in CATEGORIES, "Unknown category")
        _require(row["act"] is None or row["act"] in ACTS, "Unknown act")
        _require(row["provenance"] in PROVENANCES, "Unknown provenance")
        _require(type(row["needs_legal_review"]) is bool, "Review flag must be boolean")
        gold = row["gold_citation_ids"]
        _require(isinstance(gold, list) and bool(gold), "Gold citations must be a non-empty list")
        _require(all(isinstance(cid, str) for cid in gold), "Gold IDs must be strings")
        _require(len(set(gold)) == len(gold), "Duplicate gold citation ID")
        _require(all(cid in citations for cid in gold), f"Unknown gold citation in {row['id']}")
        gold_acts = {citations[cid]["act"] for cid in gold}
        if row["act"] is not None:
            _require(gold_acts == {row["act"]}, "Act does not match gold citations")
        if row["category"] == "procedural":
            _require(row["act"] == "BNSS", "Procedural questions must target BNSS")
        if row["category"] == "cross_act":
            _require(row["act"] is None and len(gold_acts) > 1, "Cross-act targets must span acts")
        if row["category"] in ("cross_act", "scenario"):
            _require(row["provenance"] == "generated_candidate", "Expected candidate provenance")
        if row["provenance"] == "generated_candidate":
            _require(row["needs_legal_review"], "Candidates require legal review")
        if row["provenance"] == "corpus_section":
            _require(
                row["category"] == "direct_lookup" and len(gold) == 1,
                "Corpus-section rows must be single-section direct lookups",
            )
            _require("json_pointer" in row, "Corpus-section row needs a JSON pointer")
        if "json_pointer" in row:
            _require(
                isinstance(row["json_pointer"], str)
                and row["json_pointer"] in {citations[cid]["json_pointer"] for cid in gold},
                "JSON pointer must identify a gold citation's corpus node",
            )
        if "notes" in row:
            _require(isinstance(row["notes"], str), "Notes must be text")


def regenerate(
    seed: int,
    *,
    corpus_path: Path = CORPUS_PATH,
    citations_path: Path = CITATIONS_PATH,
    per_act: int = PER_ACT,
    stride: int = STRIDE,
    excluded_citation_ids: Iterable[str] = GOLD_SEED_IDS,
) -> list[dict[str, Any]]:
    """Regenerate only corpus_section rows; curated rows remain in questions_v2.

    For each act in ACTS order, sort section entries lexically by citation_id,
    remove exclusions, then take (seed + i * stride) % section_count. A stride
    coprime to each pool size prevents repeated sections. IDs derive from the
    selected citation rather than row order. No random or retrieval service runs.
    """
    _require(type(seed) is int and seed >= 0, "Seed must be a non-negative integer")
    _require(type(per_act) is int and per_act > 0, "per_act must be a positive integer")
    _require(type(stride) is int and stride > 0, "Stride must be a positive integer")
    corpus = _read_json(corpus_path)
    citations = load_citations(citations_path)
    excluded = set(excluded_citation_ids)
    _require(excluded <= citations.keys(), "Unknown excluded citation ID")
    sections = sorted(
        (entry for cid, entry in citations.items() if entry["kind"] == "section" and cid not in excluded),
        key=lambda entry: entry["citation_id"],
    )
    rows = []
    for act in ACTS:
        pool = [entry for entry in sections if entry["act"] == act]
        _require(len(pool) >= per_act, f"Too few sections for {act}")
        _require(math.gcd(stride, len(pool)) == 1, f"Stride must be coprime to {act} pool size")
        for index in range(per_act):
            entry = pool[(seed + index * stride) % len(pool)]
            section = _resolve_pointer(corpus, entry["json_pointer"])
            _require(
                section["section_number"] == entry["section_number"],
                "Citation pointer resolves to a different section",
            )
            title = " ".join(section["title"].split()).rstrip(".")
            _require(bool(title), "Missing section title")
            rows.append({
                "id": f"p34-02-direct-{entry['citation_id']}",
                "query": f"Which provision in the ingested {act} 2023 snapshot covers '{title}'?",
                "act": act,
                "category": "direct_lookup",
                "gold_citation_ids": [entry["citation_id"]],
                "provenance": "corpus_section",
                "json_pointer": entry["json_pointer"],
                "needs_legal_review": False,
                "notes": "Title lookup from the pinned corpus; no legal application assessed.",
            })
    validate_questions(rows, citations)
    return rows


def load_dataset(
    path: Path = QUESTIONS_PATH,
    *,
    manifest_path: Path = MANIFEST_PATH,
    citations_path: Path = CITATIONS_PATH,
    corpus_path: Path = CORPUS_PATH,
    chunks_path: Path = CHUNKS_PATH,
) -> list[dict[str, Any]]:
    """Load v2 questions after offline schema, manifest, and snapshot checks.

    The pinned reference, citations.json and chunks.jsonl must be present locally.
    Missing artifacts fail explicitly; no downloads or live services are used.
    """
    dataset = _read_json(path)
    manifest = _read_json(manifest_path)
    _require(dataset["version"] == manifest["version"] == "v2", "Expected dataset version v2")
    rows = dataset["questions"]
    citations = load_citations(citations_path)
    validate_questions(rows, citations)
    _require(len(rows) >= 30, "P34-02 requires at least 30 unique questions")
    _require(type(manifest["total_questions"]) is int, "Question total must be an integer")
    _require(manifest["total_questions"] == len(rows), "Manifest question count differs")
    categories = dict(Counter(row["category"] for row in rows))
    _require(set(categories) == set(CATEGORIES), "All four categories are required")
    _require(manifest["categories"] == categories, "Manifest category counts differ")
    acts = dict(Counter(row["act"] for row in rows if row["act"] is not None))
    _require(set(acts) == set(ACTS), "All three acts are required")
    acts["null"] = sum(row["act"] is None for row in rows)
    _require(manifest["acts"] == acts, "Manifest act counts differ")
    _require(categories["direct_lookup"] > len(rows) / 2, "Direct lookups must be the majority")
    provenances = Counter(row["provenance"] for row in rows)
    _require(3 <= provenances["gold_retrieval"] <= 5, "Expected 3-5 retrieval seeds")
    _require(8 <= provenances["generated_candidate"] <= 12, "Expected 8-12 manual candidates")
    timestamp = manifest["generated_at"]
    _require(isinstance(timestamp, str), "generated_at must be ISO 8601 text")
    _require(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is not None,
             "generated_at must include a timezone")
    for key, artifact, pin in (
        ("corpus_sha256", corpus_path, CORPUS_SHA256),
        ("chunks_sha256", chunks_path, CHUNKS_SHA256),
    ):
        _require(manifest[key] == pin == file_sha256(artifact), f"{key} snapshot mismatch")
    _require(
        re.fullmatch(r"[0-9a-f]{64}", manifest["citations_sha256"]) is not None
        and manifest["citations_sha256"] == file_sha256(citations_path),
        "citations_sha256 snapshot mismatch",
    )
    generator = manifest["generator"]
    _require(generator["identity"] == GENERATOR_ID, "Unknown generator identity")
    generated = regenerate(
        manifest["seed"], corpus_path=corpus_path, citations_path=citations_path,
        per_act=generator["per_act"], stride=generator["stride"],
        excluded_citation_ids=generator["excluded_citation_ids"],
    )
    _require(
        [row for row in rows if row["provenance"] == "corpus_section"] == generated,
        "Corpus-section rows differ from deterministic regeneration",
    )
    return rows
