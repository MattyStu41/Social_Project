"""Backup + restore round-trip tests (Deliverable 12)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from models import PlatformToken, PostStatus, ScheduledPost


@pytest.mark.asyncio
async def test_backup_redacts_token_secrets(engine, db_session, tmp_path):
    from scripts.backup import build_backup

    db_session.add(
        PlatformToken(
            platform="threads",
            access_token="SECRET-TOKEN",
            refresh_token="ALSO-SECRET",
            user_id="u",
            account_label="@u",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db_session.commit()

    payload = await build_backup()
    tokens = payload["platform_tokens"]
    assert len(tokens) == 1
    assert tokens[0]["access_token"] == "***REDACTED***"
    assert tokens[0]["refresh_token"] == "***REDACTED***"
    # Non-secret columns survive.
    assert tokens[0]["user_id"] == "u"
    assert tokens[0]["platform"] == "threads"


@pytest.mark.asyncio
async def test_backup_then_restore_round_trip(engine, db_session, tmp_path, session_factory):
    from scripts.backup import build_backup
    from scripts.restore import _restore_table

    pid = uuid.uuid4()
    db_session.add(
        ScheduledPost(
            id=pid,
            caption="round-trip",
            platforms=["threads"],
            scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            status=PostStatus.pending,
            platform_results={},
            platform_overrides={},
            attempts=[],
        )
    )
    await db_session.commit()

    payload = await build_backup()
    backup_path = tmp_path / "b.json"
    backup_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    # Clear out and rebuild on the same in-memory engine.
    async with session_factory() as wipe:
        post = (await wipe.execute(select(ScheduledPost))).scalar_one()
        await wipe.delete(post)
        await wipe.commit()

    raw = json.loads(backup_path.read_text())
    async with session_factory() as restore_db:
        n = await _restore_table(restore_db, ScheduledPost, raw["scheduled_posts"])
        assert n == 1

    async with session_factory() as verify:
        row = (await verify.execute(select(ScheduledPost).where(ScheduledPost.id == pid))).scalar_one()
        assert row.caption == "round-trip"
        assert row.platforms == ["threads"]
        assert row.status == PostStatus.pending


@pytest.mark.asyncio
async def test_backup_uses_current_session_factory(engine, tmp_path):
    """Smoke that the backup module honors the test-time engine swap rather
    than capturing the import-time module-level handles."""
    from scripts.backup import main_async

    out = tmp_path / "smoke.json"
    rc = await main_async(out)
    assert rc == 0
    body = json.loads(out.read_text())
    assert body["schema_version"] == "1"
    assert body["scheduled_posts"] == []
    assert body["platform_tokens"] == []
