"""Analytics endpoints (Deliverable 8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import PostAnalytics, ScheduledPost
from security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/posts")
async def list_post_analytics(
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Most-recent posts with their analytics samples grouped per platform."""
    posts = (
        await db.execute(
            select(ScheduledPost).order_by(desc(ScheduledPost.scheduled_at)).limit(limit)
        )
    ).scalars().all()
    if not posts:
        return []
    ids = [p.id for p in posts]
    samples = (
        await db.execute(select(PostAnalytics).where(PostAnalytics.post_id.in_(ids)))
    ).scalars().all()
    by_post: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for s in samples:
        by_post.setdefault(s.post_id, []).append(
            {
                "platform": s.platform,
                "interval": s.interval_label,
                "sampled_at": s.sampled_at.isoformat(),
                "snapshot": s.snapshot,
                "error": s.error,
            }
        )
    return [
        {
            "id": str(p.id),
            "caption": p.caption[:140],
            "platforms": p.platforms,
            "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
            "status": p.status.value if hasattr(p.status, "value") else str(p.status),
            "samples": by_post.get(p.id, []),
        }
        for p in posts
    ]


@router.get("/weekly")
async def weekly_aggregates(
    db: AsyncSession = Depends(get_db),
    weeks: int = 8,
) -> list[dict[str, Any]]:
    """Per-week aggregates: how many posts published per platform in each
    of the last `weeks` weeks. Engagement aggregates depend on what the
    snapshot JSON contains, so we surface the count of posts that
    successfully reported metrics for the +24h interval."""
    cutoff = datetime.now(UTC) - timedelta(weeks=weeks)
    rows = (
        await db.execute(
            select(ScheduledPost).where(ScheduledPost.scheduled_at >= cutoff)
        )
    ).scalars().all()
    samples_24h = (
        await db.execute(
            select(PostAnalytics).where(PostAnalytics.interval_label == "+24h")
        )
    ).scalars().all()
    samples_by_post: dict[uuid.UUID, list[PostAnalytics]] = {}
    for s in samples_24h:
        samples_by_post.setdefault(s.post_id, []).append(s)

    buckets: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.scheduled_at:
            continue
        sa = r.scheduled_at if r.scheduled_at.tzinfo else r.scheduled_at.replace(tzinfo=UTC)
        # ISO week start
        iso = sa.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        b = buckets.setdefault(
            key,
            {
                "week": key,
                "posts": 0,
                "platforms": {"threads": 0, "instagram": 0, "tiktok": 0},
                "samples_with_metrics": 0,
            },
        )
        b["posts"] += 1
        for p in r.platforms or []:
            if p in b["platforms"]:
                b["platforms"][p] += 1
        for s in samples_by_post.get(r.id, []):
            if s.snapshot and not s.error:
                b["samples_with_metrics"] += 1
    return sorted(buckets.values(), key=lambda x: x["week"])


@router.post("/posts/{post_id}/refetch")
async def refetch_for_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Re-sample the +24h interval for each platform on a specific post.
    Useful when the process restart dropped a queued APScheduler job."""
    from services.analytics import sample_one

    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    fetched = []
    accounts = post.platform_accounts or {}
    for platform, block in (post.platform_results or {}).items():
        external = block.get("post_id") or block.get("publish_id")
        if not external:
            continue
        # D16: use the account that was pinned at publish time, if any.
        pinned = accounts.get(platform)
        account_id_uuid: uuid.UUID | None = None
        if pinned:
            try:
                account_id_uuid = uuid.UUID(str(pinned))
            except (TypeError, ValueError):
                account_id_uuid = None
        await sample_one(
            db,
            post_id=post_id,
            platform=platform,
            external_id=str(external),
            interval_label="+24h",
            account_id=account_id_uuid,
        )
        fetched.append(platform)
    return {"refetched": fetched}
