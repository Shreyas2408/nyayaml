"""Create the append-only query_jobs audit table (P34-04).

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("act", sa.String(8), nullable=True),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("query_type", sa.String(40), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("retrieved_citation_ids", postgresql.JSONB(), nullable=True),
        sa.Column("citations_valid", sa.Boolean(), nullable=True),
        sa.Column("hallucination_flags", postgresql.JSONB(), nullable=True),
        sa.Column("disclaimers", postgresql.JSONB(), nullable=True),
        sa.Column("corpus_sha256", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # Populated by P34-05/06; intentionally nullable with no foreign key.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('retrieval-only','with-llm','api')", name="ck_query_jobs_mode"
        ),
        sa.CheckConstraint(
            "status IN ('completed','failed')", name="ck_query_jobs_status"
        ),
    )
    op.create_index("ix_query_jobs_created_at", "query_jobs", ["created_at"])
    op.create_index("ix_query_jobs_status", "query_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("query_jobs")
