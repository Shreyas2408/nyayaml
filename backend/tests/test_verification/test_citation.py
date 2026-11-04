"""Unit tests for citation verification."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from backend.app.query.orchestrator import QueryOrchestrator
from backend.app.query.schemas import QueryRequest

from backend.app.verification.citation import (
    CitationVerifier,
    UnresolvedCitationError,
)

KNOWN = "BNS-2023-ff92dcc72778-S103"
KNOWN_2 = "BNSS-2023-ff92dcc72778-S42"
MISSING = "BNS-2023-000000000000-S999"


class CitationVerifierTests(unittest.TestCase):
    def test_known_citation_verifies_as_existing(self):
        verifier = CitationVerifier([KNOWN])
        result = verifier.verify(KNOWN)
        self.assertEqual(result.citation_id, KNOWN)
        self.assertTrue(result.exists)

    def test_well_formed_but_unknown_citation_is_flagged_not_found(self):
        verifier = CitationVerifier([KNOWN])
        result = verifier.verify(MISSING)
        self.assertFalse(result.exists)
        self.assertEqual(result.reason, "not_found")

    def test_malformed_citation_ids_are_flagged(self):
        verifier = CitationVerifier([KNOWN])
        bad_ids = [
            "",
            "BNS-2023-ff92dcc72778",  # no section
            "IPC-1860-ff92dcc72778-S103",  # unknown act
            "BNS-23-ff92dcc72778-S103",  # bad year
            "BNS-2023-S103",  # no corpus hash
        ]
        for citation_id in bad_ids:
            with self.subTest(citation_id=citation_id):
                result = verifier.verify(citation_id)
                self.assertFalse(result.exists)
                self.assertEqual(result.reason, "malformed")

    def test_batch_reports_unresolved_ids(self):
        verifier = CitationVerifier([KNOWN, KNOWN_2])
        report = verifier.verify_many([KNOWN, MISSING])
        self.assertEqual(report.verified_ids, [KNOWN])
        self.assertEqual(report.unresolved_ids, [MISSING])
        self.assertFalse(report.is_valid)

    def test_batch_of_resolvable_citations_is_valid(self):
        verifier = CitationVerifier([KNOWN, KNOWN_2])
        report = verifier.verify_many([KNOWN, KNOWN_2])
        self.assertEqual(report.verified_ids, [KNOWN, KNOWN_2])
        self.assertEqual(report.unresolved_ids, [])
        self.assertTrue(report.is_valid)

    def test_canonical_hyphenated_ids_resolve_by_exact_corpus_membership(self):
        ids = [
            "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R005",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P213-R010",
            "BNSS-2023-8047fe1de609-S359-SS1-T1-R001",
            "BNSS-2023-8047fe1de609-SCH1-NOTES",
            "BNSS-2023-8047fe1de609-SCH2-INTRO",
            "BNSS-2023-8047fe1de609-SCH2-FORM1",
        ]
        report = CitationVerifier(ids).verify_many(ids)
        self.assertEqual(report.verified_ids, ids)
        self.assertEqual(report.unresolved_ids, [])
        self.assertTrue(report.is_valid)

    def test_unknown_schedule_row_still_fails_corpus_membership(self):
        known = "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R005"
        unknown = "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R999"
        result = CitationVerifier([known]).verify(unknown)
        self.assertFalse(result.exists)
        self.assertEqual(result.reason, "not_found")

    def test_empty_id_segments_and_prose_references_are_not_citation_ids(self):
        bad_ids = [
            "BNSS-2023-8047fe1de609-SCH1--PART1-P207-R005",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R005-",
            "[1]",
            "Section 303",
            "§ 303",
            "303(2)",
            "BNS",
        ]
        verifier = CitationVerifier(bad_ids)
        for citation_id in bad_ids:
            with self.subTest(citation_id=citation_id):
                result = verifier.verify(citation_id)
                self.assertFalse(result.exists)
                self.assertEqual(result.reason, "malformed")

    def test_query_validity_does_not_depend_on_inline_markers(self):
        ids = [
            "BNS-2023-ff92dcc72778-S303",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R005",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P213-R010",
        ]
        answer = (
            "Imprisonment for a term of either description up to three years, "
            "or with fine, or with both"
        )
        hits = [
            {
                "citation_id": cid,
                "act": cid.split("-")[0],
                "compressed_text": "Synthetic retrieved passage.",
            }
            for cid in ids
        ]
        for claimed in (ids[:1], ids):
            with self.subTest(claimed=claimed):
                reranker, compressor, ollama = MagicMock(), MagicMock(), MagicMock()
                reranker.search.return_value = {"results": hits}
                compressor.compress.return_value = hits
                ollama.generate.return_value = {
                    "text": json.dumps({
                        "answer": answer,
                        "citations": [{"citation_id": cid} for cid in claimed],
                        "confidence": 0.9,
                        "query_type": "direct_lookup",
                    }),
                    "fallback_used": False,
                }
                orchestrator = QueryOrchestrator(
                    reranker=reranker, compressor=compressor, ollama=ollama,
                    known_citation_ids=ids,
                )
                response = orchestrator.run(QueryRequest(query="synthetic question"))
                self.assertEqual(response.answer, answer)
                self.assertEqual([c.citation_id for c in response.citations], claimed)
                self.assertEqual(response.retrieved_citation_ids, ids)
                self.assertEqual(response.hallucination_flags, [])
                self.assertTrue(response.citations_valid)

    def test_verify_or_raise_names_the_unresolvable_citation(self):
        verifier = CitationVerifier([KNOWN])
        with self.assertRaises(UnresolvedCitationError) as ctx:
            verifier.verify_or_raise(MISSING)
        self.assertIn(MISSING, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
