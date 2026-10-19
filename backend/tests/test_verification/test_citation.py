"""Unit tests for citation verification."""

from __future__ import annotations

import unittest

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

    def test_verify_or_raise_names_the_unresolvable_citation(self):
        verifier = CitationVerifier([KNOWN])
        with self.assertRaises(UnresolvedCitationError) as ctx:
            verifier.verify_or_raise(MISSING)
        self.assertIn(MISSING, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()