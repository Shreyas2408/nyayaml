"""Parse raw LLM text into a validated LLMResponse.

Ollama does not guarantee clean JSON. The same model may return an object,
a ```json fenced block, or the object buried in prose. ``parse_llm_response``
handles all three and raises one of two typed errors:

- ``JSONParsingError``: no parseable JSON object in the text.
- ``SchemaValidationError``: JSON parsed, but it violates the response contract.

The contract mirrors ``backend/app/prompts/schemas/response_schema.json``.
Keep the two in sync.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# The seven query categories used by the analyzer and prompt registry.
QueryType = Literal[
    "direct_lookup",
    "scenario",
    "cross_act",
    "procedural",
    "out_of_scope",
    "definition",
    "punishment",
]

_FENCED_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class StructuredOutputError(ValueError):
    """Base class so callers can catch every parse/validate failure at once."""


class JSONParsingError(StructuredOutputError):
    """The model output contained no parseable JSON object."""


class SchemaValidationError(StructuredOutputError):
    """The JSON parsed, but does not satisfy the response contract."""


class Citation(BaseModel):
    """One passage the answer relies on.

    citation_id is required because it is the key verified against the
    canonical corpus; the remaining fields are presentational.
    """

    model_config = ConfigDict(extra="ignore")

    citation_id: str = Field(min_length=1)
    act: Literal["BNS", "BNSS", "BSA"] | None = None
    section: str | None = None
    title: str | None = None
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)


class LLMResponse(BaseModel):
    """A validated answer, ready for verification and display."""

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1)
    citations: list[Citation]
    confidence: float = Field(ge=0.0, le=1.0)
    query_type: QueryType | None = None

    @field_validator("answer")
    @classmethod
    def answer_must_not_be_blank(cls, value: str) -> str:
        # min_length catches "", but whitespace-only answers pass it.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("answer must not be blank")
        return cleaned


def _balanced_objects(text: str):
    """Yield every balanced {...} region, ignoring braces inside strings.

    Braces in prose ("use {braces} here") are yielded too; the caller simply
    tries each candidate until one parses.
    """
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]
                start = None


def _extract_object(text: str) -> dict[str, Any]:
    """Try the whole text, then fenced blocks, then balanced brace regions."""
    attempts: list[str] = [text.strip()]
    attempts.extend(match.group(1).strip() for match in _FENCED_BLOCK.finditer(text))
    attempts.extend(_balanced_objects(text))

    for attempt in attempts:
        if not attempt:
            continue
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise JSONParsingError("No valid JSON object found in the model output.")


def _describe(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<root>"
    return f"invalid response at {location}: {first['msg']}"


def parse_llm_response(text: str) -> LLMResponse:
    """Parse and validate one raw model answer.

    Raises JSONParsingError when no JSON object is present, or
    SchemaValidationError when the object violates the contract.
    """
    if not isinstance(text, str) or not text.strip():
        raise JSONParsingError("Model output is empty.")

    data = _extract_object(text)
    try:
        return LLMResponse.model_validate(data)
    except ValidationError as error:
        raise SchemaValidationError(_describe(error)) from error