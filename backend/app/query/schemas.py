"""Query schemas — Pydantic models for query request/response."""

from pydantic import BaseModel


class QueryRequest(BaseModel):
    """Schema for incoming legal query."""

    text: str


class QueryResponse(BaseModel):
    """Schema for query response."""

    id: str
    answer: str
    confidence: float
    citations: list[dict] = []
