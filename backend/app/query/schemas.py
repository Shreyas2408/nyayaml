"""Pydantic contracts for the query pipeline (P34-01)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Act = Literal["BNS", "BNSS", "BSA"]

DISCLAIMERS = ["This is informational only and is not legal advice."]


class QueryRequest(BaseModel):
    """Inbound legal question. Empty query is allowed; the orchestrator short-circuits."""

    query: str = ""
    act: Act | None = None
    top_k: int = Field(default=3, ge=1, le=10)


class Citation(BaseModel):
    """One grounded passage shown to the caller. extra=ignore so hit dicts can be projected."""

    model_config = ConfigDict(extra="ignore")

    citation_id: str = Field(min_length=1)
    act: Act | None = None
    header: str | None = None
    chunk_id: str | None = None


class QueryResponse(BaseModel):
    """Fail-closed answer. citations is only ids that were retrieved AND in the corpus."""

    query: str = ""
    act: Act | None = None
    top_k: int = 3
    answer: str | None = None
    query_type: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    citations_valid: bool = True
    hallucination_flags: list[str] = Field(default_factory=list)
    retrieved_citation_ids: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    disclaimers: list[str] = Field(default_factory=lambda: list(DISCLAIMERS))
    error: str | None = None
