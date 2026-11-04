"""Unit tests for the Ollama client and its fallback chain.

No request reaches the network: every HTTP call goes through an
httpx.MockTransport that plays the Ollama server.
"""

from __future__ import annotations

import json
import os
import unittest

import httpx

from backend.app.llm.client import LLMUnavailable, OllamaClient, PRIMARY_MODEL
from backend.app.llm.fallback import FALLBACK_MODEL, OllamaCallError, complete


def answer(model: str, text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "message": {"role": "assistant", "content": text},
            "done": True,
        },
    )


class ScriptedOllama:
    """A fake Ollama server that plays back scripted responses in order."""

    def __init__(self, items):
        self.items = list(items)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if not self.items:
            raise AssertionError("the client made more requests than the test scripted")
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self):
        return httpx.MockTransport(self)


class OllamaClientTests(unittest.TestCase):
    def client(self, server, **overrides):
        settings = {"transport": server.transport}
        settings.update(overrides)
        return OllamaClient(**settings)

    def test_primary_success(self):
        server = ScriptedOllama([answer(PRIMARY_MODEL, "Murder is defined in BNS 103.")])
        result = self.client(server).generate("What is murder?")
        self.assertEqual(result["model"], PRIMARY_MODEL)
        self.assertFalse(result["fallback_used"])
        self.assertFalse(result["cached"])
        self.assertEqual(result["text"], "Murder is defined in BNS 103.")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertTrue(result["attempts"][0]["ok"])
        self.assertEqual(server.requests[0]["model"], PRIMARY_MODEL)
        self.assertFalse(server.requests[0]["stream"])

    def test_timeout_falls_back_to_phi3(self):
        server = ScriptedOllama(
            [
                httpx.ReadTimeout("primary timed out"),
                answer(FALLBACK_MODEL, "Fallback answer."),
            ]
        )
        result = self.client(server).generate("Where is bail defined?")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["model"], FALLBACK_MODEL)
        self.assertFalse(result["attempts"][0]["ok"])
        self.assertIn("timed out", result["attempts"][0]["error"])
        self.assertTrue(result["attempts"][1]["ok"])
        self.assertEqual(server.requests[1]["model"], FALLBACK_MODEL)

    def test_http_error_falls_back(self):
        server = ScriptedOllama(
            [
                httpx.Response(500, json={"error": "model crashed"}),
                answer(FALLBACK_MODEL, "Fallback answer."),
            ]
        )
        result = self.client(server).generate("What is an FIR?")
        self.assertTrue(result["fallback_used"])
        self.assertIn("500", result["attempts"][0]["error"])

    def test_empty_answer_counts_as_failure(self):
        server = ScriptedOllama(
            [
                answer(PRIMARY_MODEL, "   "),
                answer(FALLBACK_MODEL, "Fallback answer."),
            ]
        )
        result = self.client(server).generate("What is bail?")
        self.assertEqual(result["attempts"][0]["error"], "empty response")
        self.assertTrue(result["fallback_used"])

    def test_both_models_failing_raises(self):
        server = ScriptedOllama(
            [
                httpx.ReadTimeout("primary timed out"),
                httpx.ConnectError("phi3 refused"),
            ]
        )
        with self.assertRaises(LLMUnavailable) as caught:
            self.client(server).generate("What is theft?")
        self.assertEqual(len(caught.exception.attempts), 2)
        self.assertIn(PRIMARY_MODEL, str(caught.exception))
        self.assertIn(FALLBACK_MODEL, str(caught.exception))

    def test_wall_clock_blocks_the_fallback(self):
        # Deadlines: start at 10, primary timeout calculated at 10 (240 s given),
        # then the clock has jumped to 400, so no budget remains for phi3.
        times = iter([10.0, 10.0, 400.0])
        server = ScriptedOllama([httpx.ReadTimeout("primary timed out")])
        client = self.client(server, clock=lambda: next(times))
        with self.assertRaises(LLMUnavailable) as caught:
            client.generate("What is theft?")
        self.assertEqual(len(server.requests), 1)  # phi3 was never asked
        self.assertEqual(len(caught.exception.attempts), 1)
        self.assertIn("wall clock", str(caught.exception))

    def test_cache_serves_repeated_prompts(self):
        server = ScriptedOllama([answer(PRIMARY_MODEL, "Answer.")])
        client = self.client(server)
        first = client.generate("What is theft?")
        second = client.generate("What is theft?")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(len(server.requests), 1)

    def test_cache_is_per_prompt(self):
        server = ScriptedOllama(
            [
                answer(PRIMARY_MODEL, "First."),
                answer(PRIMARY_MODEL, "Second."),
            ]
        )
        client = self.client(server)
        self.assertEqual(client.generate("theft")["text"], "First.")
        self.assertEqual(client.generate("robbery")["text"], "Second.")
        self.assertEqual(len(server.requests), 2)

    def test_empty_prompt_is_rejected(self):
        server = ScriptedOllama([])
        with self.assertRaises(ValueError):
            self.client(server).generate("   ")
        self.assertEqual(server.requests, [])

    def test_system_message_is_sent_first(self):
        server = ScriptedOllama([answer(PRIMARY_MODEL, "Answer.")])
        self.client(server).generate(
            "What is theft?", system="You are a legal researcher."
        )
        messages = server.requests[0]["messages"]
        self.assertEqual(
            messages[0],
            {"role": "system", "content": "You are a legal researcher."},
        )
        self.assertEqual(messages[1], {"role": "user", "content": "What is theft?"})


class FallbackHelperTests(unittest.TestCase):
    def test_complete_calls_phi3(self):
        server = ScriptedOllama([answer(FALLBACK_MODEL, "Quick answer.")])
        result = complete("What is theft?", transport=server.transport)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["model"], FALLBACK_MODEL)
        self.assertEqual(server.requests[0]["model"], FALLBACK_MODEL)

    def test_complete_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            complete("  ")

    def test_http_failure_is_wrapped(self):
        server = ScriptedOllama([httpx.ConnectError("refused")])
        with self.assertRaises(OllamaCallError):
            complete("What is theft?", transport=server.transport)


def _installed_models() -> set[str]:
    try:
        response = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        response.raise_for_status()
        return {model["name"] for model in response.json().get("models", [])}
    except Exception:
        return set()


@unittest.skipUnless(
    os.environ.get("NYAYAML_LIVE_LLM") == "1",
    "set NYAYAML_LIVE_LLM=1 to run the live Ollama test",
)
class LiveOllamaTests(unittest.TestCase):
    def test_live_generation(self):
        models = _installed_models()
        if not any(name.startswith(PRIMARY_MODEL) for name in models):
            self.skipTest(
                f"{PRIMARY_MODEL} is not installed; run: ollama pull {PRIMARY_MODEL}"
            )
        result = OllamaClient().generate(
            "State in one sentence what BNS section 103 defines."
        )
        self.assertTrue(result["text"])


if __name__ == "__main__":
    unittest.main()
