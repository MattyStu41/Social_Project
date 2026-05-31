"""Boot-time reconcile of stuck `processing` posts (F-30).

The full crash-recovery feature ships in Deliverable 9; this set covers
the minimal contract added in Deliverable 2.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import scheduler
from models import PostStatus, ScheduledPost
from tests.conftest import make_post


@pytest.mark.asyncio
async def test_reconcile_all_success_marks_published(db_session, session_factory):
    post = make_post(
        platforms=("threads",),
        status=PostStatus.processing,
        platform_results={"threads": {"success": True, "post_id": "x"}},
    )
    post_id = post.id
    db_session.add(post)
    await db_session.commit()
    await db_session.close()

    await scheduler.reconcile_processing_posts()

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        assert refreshed.status == PostStatus.published


@pytest.mark.asyncio
async def test_reconcile_partial_success_marks_failed(db_session, session_factory):
    post = make_post(
        platforms=("threads", "instagram"),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
        status=PostStatus.processing,
        platform_results={"threads": {"success": True, "post_id": "x"}},
    )
    post_id = post.id
    db_session.add(post)
    await db_session.commit()
    await db_session.close()

    await scheduler.reconcile_processing_posts()

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        assert refreshed.status == PostStatus.failed
        assert "threads" in (refreshed.last_error or "")


@pytest.mark.asyncio
async def test_reconcile_no_success_requeues_as_pending(db_session, session_factory):
    past = datetime.now(UTC) - timedelta(hours=1)
    post = make_post(
        platforms=("threads",),
        status=PostStatus.processing,
        scheduled_at=past,
    )
    post_id = post.id
    db_session.add(post)
    await db_session.commit()
    await db_session.close()

    await scheduler.reconcile_processing_posts()

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        assert refreshed.status == PostStatus.pending
        # SQLite strips tz info; compare in whichever timezone we got back.
        sa = refreshed.scheduled_at
        now = datetime.now(UTC) if sa.tzinfo else datetime.utcnow()
        assert sa >= now - timedelta(seconds=5)


@pytest.mark.asyncio
async def test_reconcile_with_no_processing_posts_is_a_noop(db_session):
    # Just ensure it does not raise on an empty table.
    await scheduler.reconcile_processing_posts()
