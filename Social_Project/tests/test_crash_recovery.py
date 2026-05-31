"""Crash recovery tests (Deliverable 9, completes F-30 + F-79)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

import scheduler
from models import PostStatus, ScheduledPost
from tests.conftest import make_post


@pytest.mark.asyncio
async def test_in_flight_platform_is_flagged_needs_manual_verify(db_session, session_factory):
    """A platform with `in_flight: True` at crash time becomes a failure
    with `needs_manual_verify: True`, and the post is `failed`."""
    post = make_post(
        platforms=("threads",),
        status=PostStatus.processing,
        platform_results={"threads": {"in_flight": True, "started_at": "2026-01-01T00:00:00+00:00"}},
    )
    db_session.add(post)
    await db_session.commit()
    post_id = post.id
    await db_session.close()

    await scheduler.reconcile_processing_posts()

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        assert refreshed.status == PostStatus.failed
        assert refreshed.platform_results["threads"]["needs_manual_verify"] is True
        assert "verify" in (refreshed.last_error or "").lower()


@pytest.mark.asyncio
async def test_mixed_in_flight_and_success(db_session, session_factory):
    """Threads succeeded before crash; Instagram was in-flight. Result: failed,
    threads success preserved, instagram flagged needs_manual_verify."""
    post = make_post(
        platforms=("threads", "instagram"),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
        status=PostStatus.processing,
        platform_results={
            "threads": {"success": True, "post_id": "thr-1"},
            "instagram": {"in_flight": True, "started_at": "2026-01-01T00:00:00+00:00"},
        },
    )
    db_session.add(post)
    await db_session.commit()
    post_id = post.id
    await db_session.close()

    await scheduler.reconcile_processing_posts()

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        assert refreshed.status == PostStatus.failed
        assert refreshed.platform_results["threads"]["success"] is True
        assert refreshed.platform_results["instagram"]["needs_manual_verify"] is True


@pytest.mark.asyncio
async def test_publish_one_commits_per_platform(seeded_tokens, db_session, session_factory):
    """F-79: after each platform attempt, platform_results is persisted so a
    crash leaves the operator with a useful audit trail."""
    post = make_post(platforms=("threads",), media_url=None, media_type=None)
    db_session.add(post)
    await db_session.commit()
    post_id = post.id

    seen_in_flight: dict = {}

    async def fake_threads(*, access_token, user_id, caption, media_url, media_type):
        # While this fake is running, the row in the DB should have an
        # in_flight marker for threads.
        async with session_factory() as peek:
            from sqlalchemy import select as sel
            row = (await peek.execute(sel(ScheduledPost).where(ScheduledPost.id == post_id))).scalar_one()
            seen_in_flight["state"] = (row.platform_results or {}).get("threads", {})
        return "thr-1"

    import services.meta as meta

    meta.publish_to_threads = fake_threads

    from models import PlatformToken

    rows = (await db_session.execute(select(PlatformToken))).scalars().all()
    tokens = scheduler._build_token_index(rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen_in_flight["state"].get("in_flight") is True
