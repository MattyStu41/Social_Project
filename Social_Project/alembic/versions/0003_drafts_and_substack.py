"""Drafts + Substack RSS fan-out tables (Deliverable 5).

* `drafts` holds review-queue items (Substack-sourced or otherwise). They
  never enter the scheduler; the operator promotes them into scheduled_posts
  by hitting POST /api/drafts/{id}/promote.
* `substack_seen` records every (feed_url, item_guid) we have already turned
  into a draft, so the 15-minute poller does not double-create.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_kind", sa.String(40), nullable=False),  # 'substack' | 'manual'
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("source_guid", sa.String(255), nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("pull_quotes", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("suggested_caption", sa.Text, nullable=True),
        sa.Column("media_url", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("promoted_post_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_drafts_source_kind_created_at", "drafts", ["source_kind", "created_at"])

    op.create_table(
        "substack_seen",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("feed_url", sa.Text, nullable=False),
        sa.Column("guid", sa.String(255), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("feed_url", "guid", name="uq_substack_seen_feed_guid"),
    )


def downgrade() -> None:
    op.drop_table("substack_seen")
    op.drop_index("ix_drafts_source_kind_created_at", table_name="drafts")
    op.drop_table("drafts")
