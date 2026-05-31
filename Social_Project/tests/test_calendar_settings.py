"""Calendar reschedule + settings tests (Deliverable 7)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from models import PostStatus, ScheduledPost


def _future(hours: int = 1) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@pytest.mark.asyncio
async def test_settings_defaults_and_patch(engine):
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        r = c.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["timezone"] == "UTC"  # default

        r = c.patch("/api/settings", json={"timezone": "America/New_York"})
        assert r.status_code == 200
        assert r.json()["timezone"] == "America/New_York"

        # GET should reflect the persisted value.
        r = c.get("/api/settings")
        assert r.json()["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_reschedule_via_patch(engine, db_session, session_factory):
    """Drag-and-drop on the calendar issues a PATCH with the new scheduled_at."""
    from fastapi.testclient import TestClient

    from main import app

    original = datetime.now(UTC) + timedelta(days=1)
    new_time = original + timedelta(days=3)
    post = ScheduledPost(
        id=uuid.uuid4(),
        caption="reschedule me",
        platforms=["threads"],
        scheduled_at=original,
        status=PostStatus.pending,
        platform_results={},
        platform_overrides={},
        attempts=[],
    )
    db_session.add(post)
    await db_session.commit()
    post_id = post.id
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        r = c.patch(f"/api/posts/{post_id}", json={"scheduled_at": new_time.isoformat()})
        assert r.status_code == 200, r.text

    async with session_factory() as fresh:
        refreshed = (
            await fresh.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        # SQLite drops tz; defensively re-attach.
        actual = refreshed.scheduled_at
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=UTC)
        assert abs((actual - new_time).total_seconds()) < 5
