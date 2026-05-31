"""Analytics pull-back tests (Deliverable 8)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from models import PostAnalytics, PostStatus, ScheduledPost
from services.analytics import sample_one


@pytest.mark.asyncio
async def test_sample_one_writes_snapshot(seeded_tokens, db_session, monkeypatch):
    """A successful fetcher should yield a snapshot row keyed by interval."""
    post_id = uuid.uuid4()

    async def fake_threads(token, ext):
        return {"data": [{"name": "views", "values": [{"value": 42}]}]}

    import services.analytics as analytics

    monkeypatch.setattr(analytics, "fetch_threads_insights", fake_threads)

    await sample_one(
        db_session,
        post_id=post_id,
        platform="threads",
        external_id="thr-123",
        interval_label="+1h",
    )

    rows = (
        await db_session.execute(
            select(PostAnalytics).where(PostAnalytics.post_id == post_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].interval_label == "+1h"
    assert rows[0].snapshot["data"][0]["values"][0]["value"] == 42
    assert rows[0].error is None


@pytest.mark.asyncio
async def test_sample_one_records_error_on_fetcher_exception(seeded_tokens, db_session, monkeypatch):
    post_id = uuid.uuid4()

    async def explode(token, ext):
        raise RuntimeError("nope")

    import services.analytics as analytics

    monkeypatch.setattr(analytics, "fetch_threads_insights", explode)

    await sample_one(
        db_session,
        post_id=post_id,
        platform="threads",
        external_id="thr-x",
        interval_label="+24h",
    )

    row = (
        await db_session.execute(
            select(PostAnalytics).where(PostAnalytics.post_id == post_id)
        )
    ).scalar_one()
    assert row.error and "nope" in row.error
    assert row.snapshot == {}


@pytest.mark.asyncio
async def test_sample_one_is_idempotent_per_interval(seeded_tokens, db_session, monkeypatch):
    """Re-sampling the same (post, platform, interval) replaces, not duplicates."""
    post_id = uuid.uuid4()
    n = {"calls": 0}

    async def fake(token, ext):
        n["calls"] += 1
        return {"v": n["calls"]}

    import services.analytics as analytics

    monkeypatch.setattr(analytics, "fetch_threads_insights", fake)

    for _ in range(3):
        await sample_one(
            db_session,
            post_id=post_id,
            platform="threads",
            external_id="thr-1",
            interval_label="+24h",
        )

    rows = (
        await db_session.execute(
            select(PostAnalytics).where(PostAnalytics.post_id == post_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].snapshot["v"] == 3


@pytest.mark.asyncio
async def test_analytics_endpoints_authed(seeded_tokens, engine, db_session, session_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    post = ScheduledPost(
        id=uuid.uuid4(),
        caption="hello",
        platforms=["threads"],
        scheduled_at=datetime.now(UTC) - timedelta(days=2),
        status=PostStatus.published,
        platform_results={"threads": {"success": True, "post_id": "thr-1"}},
        platform_overrides={},
        attempts=[],
    )
    db_session.add(post)
    await db_session.commit()
    post_id = post.id

    async def fake(token, ext):
        return {"views": 100}

    import services.analytics as analytics

    monkeypatch.setattr(analytics, "fetch_threads_insights", fake)

    # Seed one sample.
    await sample_one(
        db_session,
        post_id=post_id,
        platform="threads",
        external_id="thr-1",
        interval_label="+24h",
    )
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})

        r = c.get("/api/analytics/posts")
        assert r.status_code == 200
        body = r.json()
        assert any(item["id"] == str(post_id) for item in body)
        my = next(item for item in body if item["id"] == str(post_id))
        assert any(s["snapshot"].get("views") == 100 for s in my["samples"])

        r = c.get("/api/analytics/weekly")
        assert r.status_code == 200
        weekly = r.json()
        assert isinstance(weekly, list)
