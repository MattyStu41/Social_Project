"""Initial schema baseline.

Captures the pre-Alembic schema (scheduled_posts, platform_tokens, post_status enum).
On a brand-new database this creates the tables. On a database whose tables were
already created by the previous `Base.metadata.create_all` path, the operator
runs `alembic stamp head` once to mark the existing schema as up-to-date — see
docs/DEPLOY_REPLIT.md for the runbook.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PostgresEnum
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_type=False so op.create_table below does NOT auto-emit a second
    # CREATE TYPE (which fails on Postgres with "type post_status already
    # exists"). The explicit create() with checkfirst=True is the single,
    # idempotent creator. SQLite never runs migrations (it uses create_all),
    # so the Postgres-specific ENUM here is safe.
    post_status = PostgresEnum(
        "pending",
        "processing",
        "published",
        "failed",
        "cancelled",
        name="post_status",
        create_type=False,
    )
    post_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "scheduled_posts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("caption", sa.Text, nullable=False),
        sa.Column("platforms", sa.JSON, nullable=False),
        sa.Column("media_url", sa.Text, nullable=True),
        sa.Column("media_type", sa.String(20), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            post_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("platform_results", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("attempts", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_scheduled_posts_status_scheduled_at",
        "scheduled_posts",
        ["status", "scheduled_at"],
    )

    op.create_table(
        "platform_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.String(50), nullable=False, unique=True),
        sa.Column("access_token", sa.Text, nullable=False),
        sa.Column("refresh_token", sa.Text, nullable=True),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("account_label", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("platform_tokens")
    op.drop_index("ix_scheduled_posts_status_scheduled_at", table_name="scheduled_posts")
    op.drop_table("scheduled_posts")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS post_status")
