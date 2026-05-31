"""Analytics pull-back (Deliverable 8).

After a successful publish, the scheduler queues four follow-up jobs at +1h,
+24h, +72h, +7d. Each job calls a per-platform fetcher and persists the
returned snapshot. If a platform's API doesn't expose metrics on the scope
the operator has, the row is still written with an `error` column so the
operator can see "this is a known limitation" instead of silently missing
data.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings

log = logging.getLogger("jack.analytics")

INTERVALS = [
    ("+1h", timedelta(hours=1)),
    ("+24h", timedelta(hours=24)),
    ("+72h", timedelta(hours=72)),
    ("+7d", timedelta(days=7)),
]

_GRAPH = f"https://graph.facebook.com/{settings.meta_graph_version}"
_THREADS = "https://graph.threads.net/v1.0"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


async def fetch_threads_insights(access_token: str, external_id: str) -> dict[str, Any]:
    """Threads exposes views/likes/replies/reposts/quotes on the media insights endpoint."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_THREADS}/{external_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": access_token,
            },
        )
        if r.status_code >= 400:
            return {"_error": f"threads insights {r.status_code}: {r.text[:200]}"}
        return r.json()


async def fetch_instagram_insights(access_token: str, external_id: str) -> dict[str, Any]:
    """Instagram Graph API: insights on the media id."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{_GRAPH}/{external_id}/insights",
            params={
                "metric": "impressions,reach,engagement,likes,comments,shares,saved",
                "access_token": access_token,
            },
        )
        if r.status_code >= 400:
            return {"_error": f"instagram insights {r.status_code}: {r.text[:200]}"}
        return r.json()


async def fetch_tiktok_metrics(access_token: str, external_id: str) -> dict[str, Any]:
    """TikTok Content Posting API only exposes publish status, not engagement.
    Record that limitation as a snapshot so the operator can see it."""
    from services.tiktok import get_publish_status

    try:
        status = await get_publish_status(access_token, external_id)
    except Exception as exc:
        return {"_error": f"tiktok status_fetch failed: {exc}"}
    return {
        "publish_status": status,
        "note": (
            "TikTok Content Posting API does not expose engagement metrics. "
            "Drafts uploaded by this scheduler are finalised in the TikTok app; "
            "engagement is visible there."
        ),
    }


async def sample_one(
    db: AsyncSession,
    *,
    post_id: uuid.UUID,
    platform: str,
    external_id: str,
    interval_label: str,
    account_id: uuid.UUID | None = None,
) -> None:
    """Fetch metrics for one (post, platform, interval) tuple and upsert.

    D16: `account_id` pins the specific PlatformToken to use. If omitted
    (legacy callers), we fall back to the first-created account for that
    platform — matches the scheduler's default-account behaviour.
    """
    from models import PlatformToken, PostAnalytics

    token: Any = None
    if account_id is not None:
        token = (
            await db.execute(select(PlatformToken).where(PlatformToken.id == account_id))
        ).scalar_one_or_none()
    if token is None:
        token = (
            await db.execute(
                select(PlatformToken)
                .where(PlatformToken.platform == platform)
                .order_by(PlatformToken.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
    if token is None:
        snapshot: dict[str, Any] = {}
        err: str | None = f"{platform} not connected at sampling time."
    else:
        try:
            if platform == "threads":
                snapshot = await fetch_threads_insights(token.access_token, external_id)
            elif platform == "instagram":
                snapshot = await fetch_instagram_insights(token.access_token, external_id)
            elif platform == "tiktok":
                snapshot = await fetch_tiktok_metrics(token.access_token, external_id)
            else:
                snapshot = {}
            err = snapshot.pop("_error", None) if isinstance(snapshot, dict) else None
        except Exception as exc:
            snapshot = {}
            err = f"sample failed: {exc}"

    # Idempotent upsert keyed on (post_id, platform, interval_label).
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = (
            pg_insert(PostAnalytics)
            .values(
                id=uuid.uuid4(),
                post_id=post_id,
                platform=platform,
                external_id=external_id,
                interval_label=interval_label,
                sampled_at=datetime.now(UTC),
                snapshot=snapshot,
                error=err,
            )
            .on_conflict_do_update(
                index_elements=["post_id", "platform", "interval_label"],
                set_={"sampled_at": datetime.now(UTC), "snapshot": snapshot, "error": err},
            )
        )
        await db.execute(stmt)
    else:
        # SQLite path (tests): select-then-update.
        existing = (
            await db.execute(
                select(PostAnalytics).where(
                    PostAnalytics.post_id == post_id,
                    PostAnalytics.platform == platform,
                    PostAnalytics.interval_label == interval_label,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.sampled_at = datetime.now(UTC)
            existing.snapshot = snapshot
            existing.error = err
        else:
            db.add(
                PostAnalytics(
                    post_id=post_id,
                    platform=platform,
                    external_id=external_id,
                    interval_label=interval_label,
                    sampled_at=datetime.now(UTC),
                    snapshot=snapshot,
                    error=err,
                )
            )
    await db.commit()


async def sample_one_job(
    *,
    post_id_str: str,
    platform: str,
    external_id: str,
    interval_label: str,
    account_id_str: str | None = None,
) -> None:
    """APScheduler-compatible wrapper for sample_one. Resolves the session
    itself so APScheduler does not need to know about it."""
    from database import AsyncSessionLocal

    account_id = uuid.UUID(account_id_str) if account_id_str else None
    async with AsyncSessionLocal() as db:
        await sample_one(
            db,
            post_id=uuid.UUID(post_id_str),
            platform=platform,
            external_id=external_id,
            interval_label=interval_label,
            account_id=account_id,
        )


def schedule_analytics_pulls(
    post_id: uuid.UUID,
    platform: str,
    external_id: str,
    account_id: uuid.UUID | None = None,
) -> None:
    """Queue the four follow-up jobs after a successful publish.

    Uses APScheduler DateTrigger so each job fires at the right wall-clock time.
    If the process restarts between schedule and fire, the jobs are lost — for
    a personal-use scheduler that's acceptable; the operator can see "no
    analytics row for the +24h slot" and re-trigger manually via
    `/api/analytics/posts/{id}/refetch`.
    """
    from apscheduler.triggers.date import DateTrigger

    from scheduler import get_scheduler

    sched = get_scheduler()
    if not sched.running:
        # Tests + one-shot CLI: the scheduler isn't running. Skip silently.
        return
    now = datetime.now(UTC)
    for label, delta in INTERVALS:
        sched.add_job(
            sample_one_job,
            trigger=DateTrigger(run_date=now + delta),
            id=f"analytics:{post_id}:{platform}:{label}",
            replace_existing=True,
            kwargs={
                "post_id_str": str(post_id),
                "platform": platform,
                "external_id": external_id,
                "interval_label": label,
                "account_id_str": str(account_id) if account_id else None,
            },
        )
    log.info(
        "Queued analytics pull-backs for post=%s platform=%s external=%s",
        post_id,
        platform,
        external_id,
    )
