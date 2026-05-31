"""Add platform_overrides to scheduled_posts (Deliverable 4).

Per-platform caption / media overrides, used by /studio to compose one post
once and tailor it per network. Defaults to {} so existing posts are
unaffected.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_posts",
        sa.Column("platform_overrides", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("scheduled_posts", "platform_overrides")
