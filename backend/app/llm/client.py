"""Primary LLM client: llama3.1:8b, phi3:mini fallback, 300-second wall clock.

One ``generate`` call never runs longer than ``wall_clock_s``:

1. Try the primary model with its own timeout (default 240 s).
2. If it times out, errors, or returns no text, spend whatever time remains
   on the smaller fallback model (default cap 60 s).
3. If neither produces text, raise ``LLMUnavailable`` with every attempt
   recorded, so the caller can log or surface why.

Successful answers are cached per prompt. Identical prompts (evaluation
reruns, repeated demo questions) are answered without calling Ollama again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from .fallback import (
    DEFAULT_OLLAMA_URL,
    FALLBACK_MODEL,
    FALLBACK_TIMEOUT_S,
    OllamaCallError,
    post_chat,
)

PRIMARY_MODEL = "llama3.1:8b"
PRIMARY_TIMEOUT_S = 240.0
WALL_CLOCK_S = 300.0
CACHE_LIMIT = 128


class LLMError(RuntimeError):
    """Base class for failures the caller may want to handle."""


class LLMUnavailable(LLMError):
    """Neither the primary nor the fallback model produced an answer."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]):
        super().__init__(message)
        self.attempts = attempts


class OllamaClient:
    """Runs one prompt through the primary model, then the fallback model."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        primary_model: str = PRIMARY_MODEL,
        fallback_model: str = FALLBACK_MODEL,
        primary_timeout_s: float = PRIMARY_TIMEOUT_S,
        fallback_timeout_s: float = FALLBACK_TIMEOUT_S,
        wall_clock_s: float = WALL_CLOCK_S,
        cache_limit: int = CACHE_LIMIT,
        transport: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.primary_timeout_s = primary_timeout_s
        self.fallback_timeout_s = fallback_timeout_s
        self.wall_clock_s = wall_clock_s
        self.cache_limit = cache_limit
        self.transport = transport
        self._clock = clock
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def generate(self, prompt: str, *, system: str | None = None) -> dict[str, Any]:
        """Answer one prompt. Raises LLMUnavailable when both models fail."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("prompt must not be empty")

        key = self._cache_key(prompt, system)
        hit = self._cache.get(key)
        if hit is not None:
            return {**hit, "cached": True}

        deadline = self._clock() + self.wall_clock_s
        attempts: list[dict[str, Any]] = []

        primary_timeout = min(
            self.primary_timeout_s, max(deadline - self._clock(), 0.0)
        )
        result = self._attempt(
            self.primary_model, prompt, system, primary_timeout, attempts
        )
        if result is not None:
            return self._store(key, result, fallback_used=False, attempts=attempts)

        remaining = deadline - self._clock()
        if remaining <= 0:
            raise LLMUnavailable(
                f"{self.primary_model} failed and the {self.wall_clock_s:.0f} s "
                "wall clock left no time for the fallback",
                attempts,
            )

        fallback_timeout = min(self.fallback_timeout_s, remaining)
        result = self._attempt(
            self.fallback_model, prompt, system, fallback_timeout, attempts
        )
        if result is not None:
            return self._store(key, result, fallback_used=True, attempts=attempts)

        raise LLMUnavailable(
            f"{self.primary_model} and {self.fallback_model} both failed",
            attempts,
        )

    def _attempt(
        self,
        model: str,
        prompt: str,
        system: str | None,
        timeout: float,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Make one model attempt. Returns the result, or None when it failed."""
        if timeout <= 0:
            attempts.append(
                {
                    "model": model,
                    "timeout_s": 0.0,
                    "ok": False,
                    "error": "no time left in the wall clock",
                    "latency_s": 0.0,
                }
            )
            return None

        try:
            result = post_chat(
                model,
                prompt,
                base_url=self.base_url,
                timeout=timeout,
                system=system,
                transport=self.transport,
            )
        except OllamaCallError as exc:
            attempts.append(
                {
                    "model": model,
                    "timeout_s": round(timeout, 3),
                    "ok": False,
                    "error": str(exc),
                    "latency_s": None,
                }
            )
            return None

        ok = bool(result["text"])
        attempts.append(
            {
                "model": model,
                "timeout_s": round(timeout, 3),
                "ok": ok,
                "error": None if ok else "empty response",
                "latency_s": result["latency_s"],
            }
        )
        return result if ok else None

    def _cache_key(self, prompt: str, system: str | None) -> str:
        raw = f"{system or ''}\n{prompt}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _store(
        self,
        key: str,
        result: dict[str, Any],
        *,
        fallback_used: bool,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        output = {
            "text": result["text"],
            "model": result["model"],
            "fallback_used": fallback_used,
            "cached": False,
            "latency_s": round(
                sum(attempt["latency_s"] or 0.0 for attempt in attempts), 3
            ),
            "attempts": attempts,
        }
        # Oldest entries are dropped first; the cache is a convenience, never
        # a source of truth, so eviction cannot affect an answer's content.
        self._cache[key] = output
        while len(self._cache) > self.cache_limit:
            self._cache.popitem(last=False)
        return dict(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", required=True, help="Question sent to the model.")
    parser.add_argument("--system", default=None, help="Optional system instruction.")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Ollama URL. Default: $OLLAMA_BASE_URL or {DEFAULT_OLLAMA_URL}.",
    )
    args = parser.parse_args()

    client = OllamaClient(base_url=args.base_url)
    try:
        result = client.generate(args.prompt, system=args.system)
    except LLMUnavailable as exc:
        print(
            json.dumps(
                {"status": "unavailable", "attempts": exc.attempts},
                indent=2,
                ensure_ascii=False,
            )
        )
        raise SystemExit(1)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
