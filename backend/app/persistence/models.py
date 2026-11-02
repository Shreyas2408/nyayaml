"""Query audit storage records supplied responses; it does not validate them."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QueryJob(Base):
    """One durably committed query attempt, including its displayed disclaimers."""

    __tablename__ = "query_jobs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('retrieval-only','with-llm','api')", name="ck_query_jobs_mode"
        ),
        CheckConstraint(
            "status IN ('completed','failed')", name="ck_query_jobs_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    act: Mapped[str | None] = mapped_column(String(8), nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    query_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    retrieved_citation_ids: Mapped[list[str] | None] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    citations_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hallucination_flags: Mapped[list[str] | None] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    disclaimers: Mapped[list[str] | None] = mapped_column(
        postgresql.JSONB, nullable=True
    )
    corpus_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Populated by P34-05/06; intentionally nullable with no foreign key.
    user_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), nullable=True
    )
