"""
SQLAlchemy ORM models.

ScheduledPost is the queue the scheduler drains. PlatformToken stores the
OAuth credentials for each platform. Both are persisted in Neon.

Uses SQLAlchemy 2.0 typed declarative (`Mapped[T]`) so mypy sees attribute
types correctly. The on-disk schema is unchanged from the previous
``Column(...)``-style declarations; this is a pure typing migration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String, Text, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PostStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    platforms: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[PostStatus] = mapped_column(
        SAEnum(PostStatus, name="post_status"),
        default=PostStatus.pending,
        nullable=False,
    )
    platform_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Per-platform overrides (D4): {"threads": {"caption": "...", "media_url": "..."}, ...}.
    # An empty dict ({}) means "use the top-level caption/media for every platform".
    platform_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Per-platform account selection (D16): {"threads": "<token uuid>", ...}.
    # An empty dict ({}) means "use the default (first-created) account for
    # each target platform" — backward-compatible with pre-D16 rows.
    platform_accounts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    attempts: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_scheduled_posts_status_scheduled_at", "status", "scheduled_at"),
    )


class Draft(Base):
    """Review-queue items (Substack-sourced or manual). Operator promotes
    them into ScheduledPost by hitting POST /api/drafts/{id}/promote.
    """

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_guid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    pull_quotes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    suggested_caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    promoted_post_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    __table_args__ = (
        Index("ix_drafts_source_kind_created_at", "source_kind", "created_at"),
    )


class SubstackSeen(Base):
    """De-dup table for the Substack RSS poller (Deliverable 5)."""

    __tablename__ = "substack_seen"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    guid: Mapped[str] = mapped_column(String(255), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        Index("uq_substack_seen_feed_guid", "feed_url", "guid", unique=True),
    )


class AppSetting(Base):
    """Key/value operator preferences (timezone, retention default, etc.).
    Used by Deliverables 7 and 13."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class PostAnalytics(Base):
    """Per-(post, platform, interval) metrics snapshot (Deliverable 8)."""

    __tablename__ = "post_analytics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_label: Mapped[str] = mapped_column(String(20), nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_post_analytics_post_platform_interval",
            "post_id",
            "platform",
            "interval_label",
            unique=True,
        ),
    )


class AuditLog(Base):
    """Operator-visible audit trail for destructive ops (Deliverable 13)."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (
        Index("ix_audit_log_at", "at"),
    )


class PlatformToken(Base):
    __tablename__ = "platform_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No unique constraint on `platform` (since D16): the operator may connect
    # multiple accounts per platform (e.g. personal IG + brand IG). Accounts
    # are disambiguated by `(platform, user_id)` at OAuth callback time.
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
