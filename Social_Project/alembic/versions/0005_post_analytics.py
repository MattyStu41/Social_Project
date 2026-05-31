"""PostAnalytics + analytics_jobs (Deliverable 8).

post_analytics: one row per (post, platform, sample). Snapshot is JSON because
each platform exposes a different shape — Threads has views/likes/replies,
Instagram has impressions/reach/engagement, TikTok has next-to-nothing on
the Content Posting API. Operators see what's available.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_analytics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("interval_label", sa.String(20), nullable=False),  # +1h, +24h, +72h, +7d
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_post_analytics_post_platform_interval",
        "post_analytics",
        ["post_id", "platform", "interval_label"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_post_analytics_post_platform_interval", table_name="post_analytics")
    op.drop_table("post_analytics")
