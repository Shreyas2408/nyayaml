"""Unit tests for hallucination detection against retrieved spans."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.app.verification.hallucination import HallucinationDetector

RETRIEVED = "BNS-2023-ff92dcc72778-S103"
NOT_RETRIEVED = "BNS-2023-abcdef123456-S7"


class HallucinationDetectorTests(unittest.TestCase):
    def test_citation_present_in_retrieved_spans_is_grounded(self):
        detector = HallucinationDetector()
        spans = [{"citation_id": RETRIEVED, "text": "Whoever commits theft..."}]
        report = detector.detect([RETRIEVED], spans)
        self.assertEqual(report.grounded_ids, [RETRIEVED])
        self.assertEqual(report.hallucinated_ids, [])
        self.assertTrue(report.is_grounded)

    def test_citation_absent_from_retrieved_spans_is_hallucinated(self):
        detector = HallucinationDetector()
        spans = [{"citation_id": RETRIEVED, "text": "Whoever commits theft..."}]
        report = detector.detect([NOT_RETRIEVED], spans)
        self.assertEqual(report.hallucinated_ids, [NOT_RETRIEVED])
        self.assertFalse(report.is_grounded)

    def test_report_separates_grounded_from_hallucinated(self):
        detector = HallucinationDetector()
        spans = [{"citation_id": RETRIEVED, "text": "Whoever commits theft..."}]
        report = detector.detect([RETRIEVED, NOT_RETRIEVED], spans)
        self.assertEqual(report.grounded_ids, [RETRIEVED])
        self.assertEqual(report.hallucinated_ids, [NOT_RETRIEVED])
        self.assertEqual(report.grounding_ratio, 0.5)

    def test_no_citations_means_nothing_to_hallucinate(self):
        detector = HallucinationDetector()
        spans = [{"citation_id": RETRIEVED, "text": "Whoever commits theft..."}]
        report = detector.detect([], spans)
        self.assertEqual(report.grounded_ids, [])
        self.assertEqual(report.hallucinated_ids, [])
        self.assertTrue(report.is_grounded)
        self.assertEqual(report.grounding_ratio, 1.0)

    def test_duplicate_citations_are_deduplicated_preserving_order(self):
        detector = HallucinationDetector()
        spans = [{"citation_id": RETRIEVED, "text": "Whoever commits theft..."}]
        report = detector.detect([RETRIEVED, RETRIEVED, NOT_RETRIEVED], spans)
        self.assertEqual(report.grounded_ids, [RETRIEVED])
        self.assertEqual(report.hallucinated_ids, [NOT_RETRIEVED])

    def test_spans_exposing_citation_id_as_attribute_are_supported(self):
        detector = HallucinationDetector()
        spans = [SimpleNamespace(citation_id=RETRIEVED, text="Whoever commits theft...")]
        report = detector.detect([RETRIEVED], spans)
        self.assertTrue(report.is_grounded)

    def test_spans_without_citation_id_do_not_ground_anything(self):
        detector = HallucinationDetector()
        spans = [{"text": "Whoever commits theft..."}]
        report = detector.detect([RETRIEVED], spans)
        self.assertEqual(report.grounded_ids, [])
        self.assertEqual(report.hallucinated_ids, [RETRIEVED])


if __name__ == "__main__":
    unittest.main()