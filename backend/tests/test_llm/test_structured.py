"""Behavior tests for parsing structured LLM output.

The seam under test is ``parse_llm_response``: one string in, one validated
``LLMResponse`` out (or a typed error). Nothing here touches Ollama.
"""

from __future__ import annotations

import json
import unittest

from backend.app.llm.structured import (
    JSONParsingError,
    LLMResponse,
    SchemaValidationError,
    parse_llm_response,
)
from backend.app.verification.citation import CitationVerifier

VALID_BODY = (
    '{"answer": "Murder is defined in BNS section 103.", '
    '"citations": [{"citation_id": "BNS-2023-ff92dcc72778-S103", '
    '"act": "BNS", "section": "103", "title": "Murder", '
    '"relevance_score": 0.94}], '
    '"confidence": 0.9}'
)


class ParseLLMResponseTests(unittest.TestCase):
    def test_clean_json_is_parsed(self):
        result = parse_llm_response(VALID_BODY)
        self.assertIsInstance(result, LLMResponse)
        self.assertEqual(result.answer, "Murder is defined in BNS section 103.")
        self.assertEqual(result.confidence, 0.9)
        self.assertEqual(len(result.citations), 1)
        self.assertEqual(
            result.citations[0].citation_id, "BNS-2023-ff92dcc72778-S103"
        )
        self.assertEqual(result.citations[0].act, "BNS")
        self.assertEqual(result.citations[0].section, "103")

    def test_canonical_schedule_citations_survive_parsing_and_verification(self):
        ids = [
            "BNS-2023-ff92dcc72778-S303",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P207-R005",
            "BNSS-2023-8047fe1de609-SCH1-PART1-P213-R010",
        ]
        answer = "A paraphrased answer without an inline citation marker."
        result = parse_llm_response(json.dumps({
            "answer": answer,
            "citations": [{"citation_id": cid} for cid in ids],
            "confidence": 0.9,
        }))
        self.assertEqual(result.answer, answer)
        self.assertEqual([c.citation_id for c in result.citations], ids)
        report = CitationVerifier(ids).verify_many(c.citation_id for c in result.citations)
        self.assertEqual(report.verified_ids, ids)
        self.assertEqual(report.unresolved_ids, [])
        self.assertTrue(report.is_valid)

    def test_markdown_fenced_json_is_parsed(self):
        text = f"```json\n{VALID_BODY}\n```"
        result = parse_llm_response(text)
        self.assertEqual(result.answer, "Murder is defined in BNS section 103.")

    def test_plain_fenced_json_is_parsed(self):
        text = f"```\n{VALID_BODY}\n```"
        self.assertEqual(len(parse_llm_response(text).citations), 1)

    def test_prose_wrapped_json_is_parsed(self):
        text = (
            "Here is the answer you asked for:\n\n"
            f"{VALID_BODY}\n\n"
            "Let me know if you need more detail."
        )
        self.assertEqual(parse_llm_response(text).confidence, 0.9)

    def test_braces_in_prose_do_not_confuse_extraction(self):
        # The first '{' opens prose, not JSON; extraction must keep looking.
        text = "Note: use {braces} carefully.\n" + VALID_BODY
        self.assertEqual(parse_llm_response(text).citations[0].section, "103")

    def test_invalid_json_syntax_raises(self):
        with self.assertRaises(JSONParsingError):
            parse_llm_response('{"answer": "broken", "citations": [')

    def test_text_without_json_raises(self):
        with self.assertRaises(JSONParsingError):
            parse_llm_response("I do not know the answer to that question.")

    def test_missing_answer_raises(self):
        text = '{"citations": [], "confidence": 0.5}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_missing_citations_raises(self):
        text = '{"answer": "x", "confidence": 0.5}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_missing_confidence_raises(self):
        text = '{"answer": "x", "citations": []}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_missing_citation_id_raises(self):
        text = (
            '{"answer": "x", '
            '"citations": [{"act": "BNS", "section": "103"}], '
            '"confidence": 0.5}'
        )
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_minimal_citation_is_accepted(self):
        text = (
            '{"answer": "x", '
            '"citations": [{"citation_id": "BNS-2023-ff92dcc72778-S103"}], '
            '"confidence": 0.5}'
        )
        result = parse_llm_response(text)
        self.assertIsNone(result.citations[0].act)
        self.assertIsNone(result.citations[0].section)

    def test_confidence_above_one_raises(self):
        text = '{"answer": "x", "citations": [], "confidence": 1.5}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_confidence_below_zero_raises(self):
        text = '{"answer": "x", "citations": [], "confidence": -0.1}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_confidence_at_bounds_is_accepted(self):
        for value in (0.0, 1.0):
            text = f'{{"answer": "x", "citations": [], "confidence": {value}}}'
            self.assertEqual(parse_llm_response(text).confidence, value)

    def test_empty_answer_raises(self):
        text = '{"answer": "   ", "citations": [], "confidence": 0.5}'
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_relevance_score_above_one_raises(self):
        text = (
            '{"answer": "x", '
            '"citations": [{"citation_id": "BNS-1", "relevance_score": 2}], '
            '"confidence": 0.5}'
        )
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_unknown_query_type_raises(self):
        # "comparative" was in the old schema; the project taxonomy has seven
        # labels, and only those are accepted.
        text = (
            '{"answer": "x", "citations": [], "confidence": 0.5, '
            '"query_type": "comparative"}'
        )
        with self.assertRaises(SchemaValidationError):
            parse_llm_response(text)

    def test_known_query_type_is_accepted(self):
        text = (
            '{"answer": "x", "citations": [], "confidence": 0.5, '
            '"query_type": "definition"}'
        )
        self.assertEqual(parse_llm_response(text).query_type, "definition")

    def test_unknown_fields_are_ignored(self):
        text = (
            '{"answer": "x", "citations": [], "confidence": 0.5, '
            '"model_notes": "extra"}'
        )
        self.assertEqual(parse_llm_response(text).answer, "x")


if __name__ == "__main__":
    unittest.main()
