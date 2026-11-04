"""The single Ollama HTTP call, plus the phi3:mini fallback helper.

Every model call in NyayaML goes through ``post_chat``: one non-streaming
request to Ollama's /api/chat endpoint, with transport and HTTP failures
wrapped into ``OllamaCallError``. ``complete`` is the convenience wrapper
that talks to the fallback model.

Timeout budgets, retry order and the overall wall clock live in ``client.py``.
This module performs exactly one attempt and reports what happened.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

FALLBACK_MODEL = "phi3:mini"
FALLBACK_TIMEOUT_S = 60.0
DEFAULT_OLLAMA_URL = "http://localhost:11434"


class OllamaCallError(RuntimeError):
    """One attempt to reach Ollama failed; the caller decides what to try next."""


def ollama_base_url(explicit: str | None = None) -> str:
    """Explicit argument, then OLLAMA_BASE_URL, then the local default.

    Docker deployments set OLLAMA_BASE_URL=http://ollama:11434. Local CLI
    runs leave it unset and reach the host's Ollama on localhost.
    """
    return explicit or os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)


def post_chat(
    model: str,
    prompt: str,
    *,
    base_url: str | None = None,
    timeout: float,
    system: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Send one chat turn and return {"model", "text", "latency_s"}."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    started = time.perf_counter()
    try:
        with httpx.Client(
            base_url=ollama_base_url(base_url),
            timeout=timeout,
            transport=transport,
        ) as client:
            response = client.post(
                "/api/chat",
                json={"model": model, "messages": messages, "stream": False},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # HTTPError covers timeouts, connection failures and non-2xx statuses.
        # ValueError covers a response body that is not valid JSON.
        raise OllamaCallError(f"{model}: {exc}") from exc

    text = ((data.get("message") or {}).get("content") or "").strip()
    return {
        "model": model,
        "text": text,
        "latency_s": round(time.perf_counter() - started, 3),
    }


def complete(
    prompt: str,
    *,
    base_url: str | None = None,
    timeout: float = FALLBACK_TIMEOUT_S,
    system: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """One-shot call to the fallback model (phi3:mini)."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    result = post_chat(
        FALLBACK_MODEL,
        prompt,
        base_url=base_url,
        timeout=timeout,
        system=system,
        transport=transport,
    )
    return {**result, "fallback": True}
