"""Create refresh_sessions table for token rotation (P34-05)."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.create_table(
        "refresh_sessions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid4,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
    )
    # The unique index enforces uniqueness and supports direct digest lookup.
    for column in ("user_id", "token_hash", "expires_at", "revoked"):
        op.create_index(
            f"ix_refresh_sessions_{column}", "refresh_sessions", [column],
            unique=column == "token_hash",
        )


def downgrade() -> None:
    op.drop_table("refresh_sessions")
    op.drop_column("users", "is_active")
