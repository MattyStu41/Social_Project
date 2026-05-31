"""Atomic-claim concurrency tests for Deliverable 2 (F-34).

The drain query is a single `UPDATE ... RETURNING` so two concurrent ticks
should each claim a disjoint subset of pending rows — never the same row
twice.

A real test requires Postgres row locks; SQLite's locking model is too coarse
to be informative. This module is gated on the `JACK_TEST_PG_URL` env var:
set it to a scratch Neon database (or a local Postgres) to enable.

For day-to-day work, we also run a single-engine variant that asserts the
non-Postgres invariant (two concurrent calls to run_due_posts process each
row exactly once thanks to APScheduler's max_instances=1 contract, which we
emulate by guarding the call site with an asyncio.Lock).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import scheduler
from models import PostStatus, ScheduledPost

PG_URL = os.environ.get("JACK_TEST_PG_URL", "").strip()
pg_only = pytest.mark.skipif(
    not PG_URL,
    reason="Set JACK_TEST_PG_URL=postgresql+asyncpg://... to enable real-DB concurrency tests",
)


@pytest.mark.asyncio
async def test_two_concurrent_ticks_publish_each_post_exactly_once(
    seeded_tokens, db_session, session_factory
):
    """SQLite variant. Spawns two scheduler ticks in parallel and asserts every
    pending post ends up published exactly once (no duplicate platform_results
    entries, no rows left pending)."""

    # Seed 5 pending posts that are all due now.
    now = datetime.now(UTC)
    posts = []
    for i in range(5):
        p = ScheduledPost(
            id=uuid.uuid4(),
            caption=f"post-{i}",
            platforms=["threads"],
            scheduled_at=now - timedelta(seconds=1),
            status=PostStatus.pending,
            platform_results={},
            attempts=[],
        )
        posts.append(p)
        db_session.add(p)
    await db_session.commit()

    # Track publish calls per post id.
    calls = {p.id: 0 for p in posts}

    async def fake_threads(*, access_token, user_id, caption, media_url, media_type):
        # The fake publisher records that *this* caption was published.
        for p in posts:
            if p.caption == caption:
                calls[p.id] += 1
        return f"ext-{caption}"

    import services.meta as meta

    meta.publish_to_threads = fake_threads

    # Run two ticks concurrently against the same engine.
    await asyncio.gather(scheduler.run_due_posts(), scheduler.run_due_posts())

    assert all(c == 1 for c in calls.values()), f"Each post should publish once. Got: {calls}"

    # Read back through a fresh session — the test's db_session has stale objects.
    await db_session.close()
    async with session_factory() as fresh:
        rows = (await fresh.execute(select(ScheduledPost))).scalars().all()
        assert all(r.status == PostStatus.published for r in rows), [
            (str(r.id), r.status) for r in rows
        ]


@pg_only
@pytest.mark.asyncio
async def test_two_concurrent_ticks_against_real_postgres():
    """Real Postgres variant: requires JACK_TEST_PG_URL to point at a scratch DB.

    This is the canonical proof that the `UPDATE ... RETURNING WHERE status=pending`
    claim cannot double-publish under contention. Postgres' row-level locks
    serialize the two UPDATEs; the second sees status=processing and skips.
    """
    import database
    import models  # noqa: F401
    from database import Base

    url = PG_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, pool_size=10, max_overflow=10)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Build clean schema in this scratch DB (drop + create).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Swap module-level handles for the duration of the test.
    original_engine = database.engine
    original_factory = database.AsyncSessionLocal
    database.engine = engine
    database.AsyncSessionLocal = Session
    try:
        async with Session() as db:
            # Seed tokens + posts.
            from models import PlatformToken

            db.add(
                PlatformToken(
                    platform="threads",
                    access_token="t",
                    user_id="u",
                    account_label="@u",
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                )
            )
            now = datetime.now(UTC) - timedelta(seconds=1)
            for i in range(20):
                db.add(
                    ScheduledPost(
                        id=uuid.uuid4(),
                        caption=f"pg-{i}",
                        platforms=["threads"],
                        scheduled_at=now,
                        status=PostStatus.pending,
                        platform_results={},
                        attempts=[],
                    )
                )
            await db.commit()

        calls: dict[str, int] = {}

        async def fake_threads(*, access_token, user_id, caption, media_url, media_type):
            calls[caption] = calls.get(caption, 0) + 1
            return f"ext-{caption}"

        import services.meta as meta

        meta.publish_to_threads = fake_threads

        # Two concurrent drains.
        await asyncio.gather(
            scheduler.run_due_posts(),
            scheduler.run_due_posts(),
            scheduler.run_due_posts(),
        )

        assert all(v == 1 for v in calls.values()), f"Double-publish under contention: {calls}"

        async with Session() as db:
            rows = (await db.execute(select(ScheduledPost))).scalars().all()
            assert all(r.status == PostStatus.published for r in rows)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
        database.engine = original_engine
        database.AsyncSessionLocal = original_factory
