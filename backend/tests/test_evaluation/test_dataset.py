"""P34-02 regression tests; local corpus artifacts only, no retrieval services.

Run from the repository root:
    python -m unittest discover -s backend/tests/test_evaluation -v
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from backend.app.evaluation.dataset import (
    ACTS,
    CATEGORIES,
    CHUNKS_PATH,
    CITATIONS_PATH,
    CORPUS_PATH,
    DEFAULT_SEED,
    GENERATOR_ID,
    GOLD_SEED_IDS,
    MANIFEST_PATH,
    PER_ACT,
    QUESTIONS_PATH,
    REQUIRED_FIELDS,
    STRIDE,
    load_citations,
    load_dataset,
    regenerate,
    validate_questions,
)


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.citations = load_citations()
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    def test_load_validates_snapshot_and_manifest_without_network(self) -> None:
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            self.assertEqual(load_dataset(), self.rows)

    def test_schema_unique_ids_and_normalized_queries(self) -> None:
        validate_questions(self.rows, self.citations)
        self.assertEqual(len(self.rows), len({row["id"] for row in self.rows}))
        self.assertEqual(
            len(self.rows),
            len({" ".join(row["query"].casefold().split()) for row in self.rows}),
        )

    def test_all_gold_ids_are_real_sections_and_pointers_resolve(self) -> None:
        for row in self.rows:
            for cid in row["gold_citation_ids"]:
                with self.subTest(question=row["id"], citation=cid):
                    self.assertIn(cid, self.citations)
                    entry = self.citations[cid]
                    self.assertEqual(entry["kind"], "section")
                    self.assertRegex(cid, r"^(BNS|BNSS|BSA)-2023-[0-9a-f]{12}-S[0-9]+$")
                    node = self.corpus
                    parts = entry["json_pointer"].strip("/").split("/")
                    for part in parts:
                        node = node[int(part)] if isinstance(node, list) else node[part]
                    self.assertEqual(node["section_number"], entry["section_number"])
                    self.assertTrue(node["title"].strip())
                    self.assertTrue(node["text"].strip())
                    self.assertEqual(
                        self.corpus["documents"][int(parts[1])]["document_name"],
                        entry["document_name"],
                    )
                    if "json_pointer" in row:
                        self.assertEqual(row["json_pointer"], entry["json_pointer"])

    def test_initial_pass_size_balance_and_all_acts(self) -> None:
        self.assertEqual(len(self.rows), 32)
        counts = Counter(row["category"] for row in self.rows)
        self.assertEqual(set(counts), set(CATEGORIES))
        self.assertEqual(counts, {"direct_lookup": 20, "procedural": 5, "cross_act": 4, "scenario": 3})
        self.assertEqual({row["act"] for row in self.rows}, {*ACTS, None})
        self.assertGreater(counts["direct_lookup"], len(self.rows) / 2)
        self.assertLess(counts["scenario"], counts["cross_act"])
        provenance = Counter(row["provenance"] for row in self.rows)
        self.assertEqual(provenance, {"gold_retrieval": 3, "corpus_section": 18, "generated_candidate": 11})
        for row in self.rows:
            if row["provenance"] == "generated_candidate":
                self.assertIs(row["needs_legal_review"], True)
                self.assertIn(row["category"], ("procedural", "cross_act", "scenario"))
            if row["category"] in ("cross_act", "scenario"):
                self.assertIsNone(row["act"])
                self.assertEqual({self.citations[cid]["act"] for cid in row["gold_citation_ids"]}, set(ACTS))
            if row["category"] == "scenario":
                self.assertIn("which provisions should be researched", row["query"])
                self.assertIn("Candidate provisions only", row["notes"])

    def test_retrieval_seed_evidence_matches_rows_and_pinned_chunks(self) -> None:
        seeds = [row for row in self.rows if row["provenance"] == "gold_retrieval"]
        self.assertEqual([row["gold_citation_ids"][0] for row in seeds], list(GOLD_SEED_IDS))
        evidence = self.manifest["retrieval_evidence"]
        self.assertEqual(evidence["chunks_sha256"], self.manifest["chunks_sha256"])
        chunks = {
            chunk["chunk_id"]: chunk
            for chunk in map(json.loads, CHUNKS_PATH.read_text(encoding="utf-8").splitlines())
        }
        self.assertEqual(len(evidence["seeds"]), len(seeds))
        by_id = {row["id"]: row for row in seeds}
        self.assertEqual({item["question_id"] for item in evidence["seeds"]}, set(by_id))
        for item in evidence["seeds"]:
            row = by_id[item["question_id"]]
            self.assertEqual(item["query"], row["query"])
            self.assertEqual(item["act"], row["act"])
            self.assertIn(item["citation_id"], row["gold_citation_ids"])
            self.assertEqual(chunks[item["chunk_id"]]["citation_id"], item["citation_id"])
            self.assertGreaterEqual(item["rank"], 1)
            self.assertLessEqual(item["rank"], 5)

    def test_regenerate_seed_matches_saved_rows_and_id_list(self) -> None:
        saved = [row for row in self.rows if row["provenance"] == "corpus_section"]
        first = regenerate(self.manifest["seed"])
        second = regenerate(self.manifest["seed"])
        self.assertEqual(first, saved)
        self.assertEqual([row["id"] for row in first], [row["id"] for row in second])
        self.assertEqual(Counter(row["act"] for row in first), {act: 6 for act in ACTS})
        self.assertEqual(len({row["gold_citation_ids"][0] for row in first}), 18)
        self.assertTrue(set(GOLD_SEED_IDS).isdisjoint(row["gold_citation_ids"][0] for row in first))
        generator = self.manifest["generator"]
        self.assertEqual(self.manifest["seed"], DEFAULT_SEED)
        self.assertEqual(generator["identity"], GENERATOR_ID)
        self.assertEqual(generator["act_order"], list(ACTS))
        self.assertEqual(generator["stride"], STRIDE)
        self.assertEqual(generator["per_act"], PER_ACT)
        self.assertEqual(generator["excluded_citation_ids"], list(GOLD_SEED_IDS))

    def test_regenerate_does_not_depend_on_citation_file_order(self) -> None:
        entries = json.loads(CITATIONS_PATH.read_text(encoding="utf-8"))
        entries["entries"].reverse()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "citations.json"
            path.write_text(json.dumps(entries), encoding="utf-8")
            self.assertEqual(regenerate(DEFAULT_SEED, citations_path=path), regenerate(DEFAULT_SEED))

    def test_different_seed_selects_different_repeatable_questions(self) -> None:
        alternate = regenerate(DEFAULT_SEED + 1)
        self.assertNotEqual([row["id"] for row in alternate], [row["id"] for row in regenerate(DEFAULT_SEED)])
        self.assertEqual(alternate, regenerate(DEFAULT_SEED + 1))

    def test_invalid_generation_parameters_fail_instead_of_repeating_sections(self) -> None:
        for parameters in (
            {"seed": True}, {"seed": -1}, {"seed": 1.5},
            {"seed": DEFAULT_SEED, "stride": 0},
            {"seed": DEFAULT_SEED, "stride": 357},
            {"seed": DEFAULT_SEED, "per_act": 1000},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                regenerate(**parameters)

    def test_missing_required_fields_are_rejected(self) -> None:
        for field in REQUIRED_FIELDS:
            row = copy.deepcopy(self.rows[0])
            del row[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_questions([row], self.citations)

    def test_malformed_fields_and_invented_gold_ids_are_rejected(self) -> None:
        changes = (
            {"id": ""}, {"query": " \t "}, {"query": 12},
            {"act": "IPC"}, {"act": "BSA"},
            {"category": "case_law"}, {"category": ["direct_lookup", "procedural"]},
            {"provenance": "assumed_gold"}, {"needs_legal_review": "true"},
            {"gold_citation_ids": []}, {"gold_citation_ids": GOLD_SEED_IDS[0]},
            {"gold_citation_ids": [None]}, {"gold_citation_ids": ["BNSS-S359"]},
            {"gold_citation_ids": ["BNSS-2023-000000000000-S359"]},
            {"gold_citation_ids": [GOLD_SEED_IDS[0], GOLD_SEED_IDS[0]]},
            {"json_pointer": "/documents/1/sections/0"}, {"notes": []},
            {"answer": "Unrequested field"},
        )
        for change in changes:
            row = {**self.rows[0], **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_questions([row], self.citations)

    def test_duplicate_ids_and_case_whitespace_variants_are_rejected(self) -> None:
        duplicate_id = {**self.rows[1], "id": self.rows[0]["id"]}
        duplicate_query = {**self.rows[0], "id": "duplicate-query", "query": "  COMPOUNDING\tOF  OFFENCES  "}
        for row in (duplicate_id, duplicate_query):
            with self.subTest(row=row["id"]), self.assertRaises(ValueError):
                validate_questions([self.rows[0], row], self.citations)

    def test_candidates_cannot_drop_review_or_claim_retrieval_provenance(self) -> None:
        for row in self.rows:
            if row["provenance"] == "generated_candidate":
                with self.subTest(question=row["id"]), self.assertRaises(ValueError):
                    validate_questions([{**row, "needs_legal_review": False}], self.citations)
            if row["category"] in ("cross_act", "scenario"):
                with self.subTest(question=row["id"]), self.assertRaises(ValueError):
                    validate_questions([{**row, "provenance": "gold_retrieval"}], self.citations)

    def test_manifest_drift_is_rejected(self) -> None:
        for change in (
            {"total_questions": 500}, {"categories": {"direct_lookup": 32}},
            {"acts": {"BNS": 32}}, {"generated_at": "2026-10-31T00:00:00"},
            {"corpus_sha256": "0" * 64}, {"chunks_sha256": "0" * 64},
            {"citations_sha256": "0" * 64}, {"seed": DEFAULT_SEED + 1},
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "MANIFEST.json"
                path.write_text(json.dumps({**self.manifest, **change}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_dataset(manifest_path=path)

    def test_changed_corpus_or_chunks_fail_snapshot_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changed-artifact.json"
            path.write_text("{}", encoding="utf-8")
            for parameter in ("corpus_path", "chunks_path"):
                with self.subTest(parameter=parameter), self.assertRaisesRegex(ValueError, "snapshot mismatch"):
                    load_dataset(**{parameter: path})

    def test_edited_generated_question_fails_regeneration_check(self) -> None:
        rows = copy.deepcopy(self.rows)
        generated = next(row for row in rows if row["provenance"] == "corpus_section")
        generated["query"] = "This is no longer the generated title lookup."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions_v2.json"
            path.write_text(json.dumps({"version": "v2", "questions": rows}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "deterministic regeneration"):
                load_dataset(path)


if __name__ == "__main__":
    unittest.main()
