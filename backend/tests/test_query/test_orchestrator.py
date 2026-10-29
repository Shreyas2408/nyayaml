"""Unit tests for QueryOrchestrator (P34-01). MiniLM and Ollama are mocked."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from backend.app.llm.client import LLMUnavailable
from backend.app.query.orchestrator import SYSTEM_PROMPT, QueryOrchestrator, main
from backend.app.query.schemas import QueryRequest
from backend.app.verification.citation import CitationVerifier
from backend.app.verification.hallucination import HallucinationDetector

GOLD_ID = "BNSS-2023-8047fe1de609-S359"
FAKE_ID = "BNS-2023-deadbeef00-S103"
COMPRESSED = "No offence shall be compounded except as provided by this section."


def _hit(**overrides):
    payload = {
        "citation_id": GOLD_ID,
        "act": "BNSS",
        "chunk_id": "chunk-1",
        "header": "BNSS §359(9) | Compounding of offences.",
        "compressed_text": COMPRESSED,
        "text": COMPRESSED,
    }
    payload.update(overrides)
    return payload


def _llm_text(citation_ids, answer="Compounding follows BNSS §359."):
    return json.dumps(
        {
            "answer": answer,
            "citations": [{"citation_id": cid} for cid in citation_ids],
            "confidence": 0.9,
            "query_type": "direct_lookup",
        }
    )


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.hit = _hit()
        self.reranker = MagicMock()
        self.compressor = MagicMock()
        self.ollama = MagicMock()
        self.reranker.search.return_value = {"results": [dict(self.hit)]}
        self.compressor.compress.return_value = [dict(self.hit)]
        self.ollama.generate.return_value = {
            "text": _llm_text([GOLD_ID]),
            "fallback_used": False,
        }
        self.orch = QueryOrchestrator(
            reranker=self.reranker,
            compressor=self.compressor,
            ollama=self.ollama,
            verifier=CitationVerifier([GOLD_ID]),
            detector=HallucinationDetector(),
        )

    def _run(self, query="compounding of offences", act="BNSS", top_k=3):
        return self.orch.run(QueryRequest(query=query, act=act, top_k=top_k))

    def test_empty_query_calls_nothing_and_returns_empty(self):
        for raw in ("", "   "):
            self.reranker.search.reset_mock()
            self.compressor.compress.reset_mock()
            self.ollama.generate.reset_mock()
            resp = self._run(query=raw)
            self.reranker.search.assert_not_called()
            self.compressor.compress.assert_not_called()
            self.ollama.generate.assert_not_called()
            self.assertIsNone(resp.answer)
            self.assertEqual(resp.citations, [])
            self.assertEqual(resp.retrieved_citation_ids, [])
            self.assertEqual(resp.hallucination_flags, [])
            self.assertIsNone(resp.error)
            self.assertTrue(resp.disclaimers)

    def test_happy_path_answer_and_citations_subset_of_retrieved(self):
        resp = self._run()
        self.assertIsNotNone(resp.answer)
        cited = [item.citation_id for item in resp.citations]
        self.assertEqual(cited, [GOLD_ID])
        self.assertTrue(set(cited) <= set(resp.retrieved_citation_ids))
        self.assertEqual(resp.hallucination_flags, [])
        self.assertTrue(resp.citations_valid)
        self.assertFalse(resp.fallback_used)
        self.ollama.generate.assert_called_once()
        kwargs = self.ollama.generate.call_args
        self.assertEqual(kwargs.kwargs.get("system") or kwargs[1].get("system"), SYSTEM_PROMPT)

    def test_prompt_contains_compressed_text_verbatim(self):
        self._run()
        prompt = self.ollama.generate.call_args.args[0]
        self.assertIn(COMPRESSED, prompt)
        self.assertIn(GOLD_ID, prompt)

    def test_citation_not_in_retrieved_spans_is_flagged_not_cited(self):
        self.ollama.generate.return_value = {
            "text": _llm_text([FAKE_ID]),
            "fallback_used": False,
        }
        resp = self._run()
        self.assertIn(FAKE_ID, resp.hallucination_flags)
        self.assertEqual([item.citation_id for item in resp.citations], [])
        self.assertFalse(resp.citations_valid)

    def test_citation_retrieved_but_not_in_corpus_is_dropped(self):
        self.orch = QueryOrchestrator(
            reranker=self.reranker,
            compressor=self.compressor,
            ollama=self.ollama,
            verifier=CitationVerifier([]),
            detector=HallucinationDetector(),
        )
        resp = self._run()
        self.assertEqual(resp.citations, [])
        self.assertFalse(resp.citations_valid)
        self.assertNotIn(GOLD_ID, resp.hallucination_flags)

    def test_parse_failure_is_fail_closed(self):
        self.ollama.generate.return_value = {
            "text": "I am not JSON",
            "fallback_used": False,
        }
        resp = self._run()
        self.ollama.generate.assert_called()
        self.assertIsNone(resp.answer)
        self.assertEqual(list(resp.citations), [])
        self.assertEqual(resp.error, "parse_error")
        self.assertEqual(resp.retrieved_citation_ids, [GOLD_ID])

    def test_no_llm_compresses_but_never_calls_ollama(self):
        out = self.orch.retrieve_and_compress(
            "compounding of offences", act="BNSS", top_k=3
        )
        self.ollama.generate.assert_not_called()
        self.reranker.search.assert_called()
        self.compressor.compress.assert_called()
        self.assertEqual(out["retrieved_citation_ids"], [GOLD_ID])
        self.assertIn(COMPRESSED, out["hits"][0]["compressed_text"])

    def test_does_not_mutate_hits_from_compressor(self):
        original = dict(self.hit)
        incoming = dict(self.hit)
        self.reranker.search.return_value = {"results": [incoming]}
        compressed = dict(self.hit)
        self.compressor.compress.return_value = [compressed]
        snapshot = dict(compressed)
        self._run()
        self.assertEqual(incoming, original)
        self.assertEqual(compressed, snapshot)

    def test_llm_unavailable_is_fail_closed(self):
        self.ollama.generate.side_effect = LLMUnavailable("down", [])
        resp = self._run()
        self.assertIsNone(resp.answer)
        self.assertEqual(list(resp.citations), [])
        self.assertEqual(resp.error, "llm_unavailable")

    def test_check_only_imports_without_constructing_models(self):
        self.assertEqual(main(["--check-only"]), 0)


if __name__ == "__main__":
    unittest.main()
