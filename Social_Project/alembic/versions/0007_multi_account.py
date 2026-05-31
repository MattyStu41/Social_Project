"""Multi-account-per-platform support (Deliverable 16).

* Drops the UNIQUE constraint on platform_tokens.platform so the operator can
  connect more than one account per platform (e.g. personal IG + brand IG).
* Adds platform_accounts JSON column on scheduled_posts. Empty {} = "use the
  default (first) account for each target platform" — backward-compatible
  with rows created before this migration.

Uses op.batch_alter_table for the DROP CONSTRAINT so the migration works on
SQLite too (SQLite cannot ALTER constraints in-place).

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite: batch-mode rebuild to drop the unique constraint.
        with op.batch_alter_table("platform_tokens", recreate="always") as batch:
            batch.alter_column("platform", existing_type=sa.String(50), nullable=False)
    else:
        # Postgres + others: drop the unique constraint by name. The
        # constraint is auto-named by SQLAlchemy when the column was created
        # with unique=True; try a couple of candidate names.
        for candidate in (
            "platform_tokens_platform_key",
            "uq_platform_tokens_platform",
        ):
            try:
                op.drop_constraint(candidate, "platform_tokens", type_="unique")
                break
            except Exception:
                continue

    op.add_column(
        "scheduled_posts",
        sa.Column(
            "platform_accounts",
            sa.JSON,
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("scheduled_posts", "platform_accounts")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # Recreating the table re-establishes whatever constraint structure
        # the model declares; without unique=True there's no constraint to
        # restore. This is a one-way migration in practice.
        pass
    else:
        op.create_unique_constraint(
            "platform_tokens_platform_key", "platform_tokens", ["platform"]
        )
