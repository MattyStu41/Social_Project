"""Retention purge + audit log tests (Deliverable 13)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from models import AuditLog, PostStatus, ScheduledPost


@pytest.mark.asyncio
async def test_purge_preview_counts_without_deleting(engine, db_session, session_factory):
    from fastapi.testclient import TestClient

    from main import app

    # Three old published, two new published, one old failed.
    for i in range(3):
        db_session.add(
            ScheduledPost(
                id=uuid.uuid4(),
                caption=f"old-{i}",
                platforms=["threads"],
                scheduled_at=datetime.now(UTC) - timedelta(days=400),
                status=PostStatus.published,
                platform_results={"threads": {"success": True}},
                platform_overrides={},
                attempts=[],
            )
        )
    for i in range(2):
        db_session.add(
            ScheduledPost(
                id=uuid.uuid4(),
                caption=f"new-{i}",
                platforms=["threads"],
                scheduled_at=datetime.now(UTC) - timedelta(days=10),
                status=PostStatus.published,
                platform_results={},
                platform_overrides={},
                attempts=[],
            )
        )
    db_session.add(
        ScheduledPost(
            id=uuid.uuid4(),
            caption="old-failed",
            platforms=["threads"],
            scheduled_at=datetime.now(UTC) - timedelta(days=400),
            status=PostStatus.failed,
            platform_results={},
            platform_overrides={},
            attempts=[],
        )
    )
    await db_session.commit()
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})

        r = c.get("/api/settings/purge?days=365")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["candidate_count"] == 3  # only old PUBLISHED

    async with session_factory() as fresh:
        all_posts = (await fresh.execute(select(ScheduledPost))).scalars().all()
        assert len(all_posts) == 6  # still six rows; preview never deletes


@pytest.mark.asyncio
async def test_purge_executes_and_is_idempotent(engine, db_session, session_factory):
    from fastapi.testclient import TestClient

    from main import app

    for i in range(2):
        db_session.add(
            ScheduledPost(
                id=uuid.uuid4(),
                caption=f"old-{i}",
                platforms=["threads"],
                scheduled_at=datetime.now(UTC) - timedelta(days=400),
                status=PostStatus.published,
                platform_results={},
                platform_overrides={},
                attempts=[],
            )
        )
    await db_session.commit()
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})

        r = c.post(
            "/api/settings/purge", json={"retention_days": 365, "confirm": True}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["deleted_count"] == 2

        # Second run on the same threshold deletes nothing.
        r = c.post(
            "/api/settings/purge", json={"retention_days": 365, "confirm": True}
        )
        assert r.status_code == 200
        assert r.json()["deleted_count"] == 0

        # Audit endpoint returns both runs.
        r = c.get("/api/settings/audit")
        assert r.status_code == 200
        actions = [e["action"] for e in r.json()]
        assert actions.count("purge_executed") == 2

    async with session_factory() as fresh:
        rows = (await fresh.execute(select(AuditLog))).scalars().all()
        assert len(rows) == 2
