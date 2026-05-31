"""Multi-account-per-platform tests (Deliverable 16)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import scheduler
from models import PlatformToken
from routers.auth import _upsert_token
from tests.conftest import make_post


async def _seed_token(db, *, platform: str, user_id: str, label: str, days: int = 30):
    tok = PlatformToken(
        platform=platform,
        access_token=f"tok-{user_id}",
        refresh_token=f"refresh-{user_id}",
        user_id=user_id,
        account_label=label,
        expires_at=datetime.now(UTC) + timedelta(days=days),
    )
    db.add(tok)
    await db.commit()
    return tok


# ---------- OAuth dedup ----------


@pytest.mark.asyncio
async def test_upsert_inserts_new_account_when_user_id_differs(db_session):
    await _upsert_token(
        db_session,
        "instagram",
        {"access_token": "a1", "expires_in": 3600},
        {"id": "ig-user-1", "label": "@one"},
    )
    await _upsert_token(
        db_session,
        "instagram",
        {"access_token": "a2", "expires_in": 3600},
        {"id": "ig-user-2", "label": "@two"},
    )
    rows = (
        await db_session.execute(select(PlatformToken).where(PlatformToken.platform == "instagram"))
    ).scalars().all()
    assert len(rows) == 2
    assert {r.user_id for r in rows} == {"ig-user-1", "ig-user-2"}


@pytest.mark.asyncio
async def test_upsert_refreshes_existing_account_when_user_id_matches(db_session):
    await _upsert_token(
        db_session,
        "threads",
        {"access_token": "stale", "expires_in": 3600},
        {"id": "th-user-1", "label": "@one"},
    )
    await _upsert_token(
        db_session,
        "threads",
        {"access_token": "fresh", "expires_in": 3600},
        {"id": "th-user-1", "label": "@one"},
    )
    rows = (
        await db_session.execute(select(PlatformToken).where(PlatformToken.platform == "threads"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].access_token == "fresh"


# ---------- Scheduler default-account fallback ----------


@pytest.mark.asyncio
async def test_scheduler_uses_first_account_when_no_pin(db_session):
    """Two IG accounts, post does not pin one → scheduler uses the
    earlier-created (`account_a`)."""
    a = await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    b = await _seed_token(db_session, platform="instagram", user_id="B", label="@b")
    # Sanity: a was created first.
    assert a.created_at <= b.created_at

    post = make_post(
        platforms=("instagram",),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
    )
    db_session.add(post)
    await db_session.commit()

    seen = {"user_id": None, "token": None}

    async def fake_ig(*, access_token, ig_user_id, caption, media_url, media_type):
        seen["user_id"] = ig_user_id
        seen["token"] = access_token
        return "ig-1"

    import services.meta as meta

    meta.publish_to_instagram = fake_ig

    rows = (
        await db_session.execute(select(PlatformToken).order_by(PlatformToken.created_at))
    ).scalars().all()
    tokens = scheduler._build_token_index(rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen["user_id"] == "A"
    assert seen["token"] == "tok-A"


@pytest.mark.asyncio
async def test_scheduler_honors_explicit_account_pin(db_session):
    """When post.platform_accounts pins account B, the scheduler uses B not A."""
    await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    b = await _seed_token(db_session, platform="instagram", user_id="B", label="@b")

    post = make_post(
        platforms=("instagram",),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
    )
    post.platform_accounts = {"instagram": str(b.id)}
    db_session.add(post)
    await db_session.commit()

    seen = {"user_id": None}

    async def fake_ig(*, access_token, ig_user_id, caption, media_url, media_type):
        seen["user_id"] = ig_user_id
        return "ig-1"

    import services.meta as meta

    meta.publish_to_instagram = fake_ig

    rows = (
        await db_session.execute(select(PlatformToken).order_by(PlatformToken.created_at))
    ).scalars().all()
    tokens = scheduler._build_token_index(rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen["user_id"] == "B"


@pytest.mark.asyncio
async def test_scheduler_falls_back_when_pinned_account_was_deleted(db_session):
    """If the pinned account is gone, the scheduler logs a warning and uses
    the platform's default (first-created) account."""
    a = await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    deleted_id = uuid.uuid4()  # never inserted

    post = make_post(
        platforms=("instagram",),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
    )
    post.platform_accounts = {"instagram": str(deleted_id)}
    db_session.add(post)
    await db_session.commit()

    seen = {"user_id": None}

    async def fake_ig(*, access_token, ig_user_id, caption, media_url, media_type):
        seen["user_id"] = ig_user_id
        return "ig-1"

    import services.meta as meta

    meta.publish_to_instagram = fake_ig

    rows = (
        await db_session.execute(select(PlatformToken).order_by(PlatformToken.created_at))
    ).scalars().all()
    tokens = scheduler._build_token_index(rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen["user_id"] == "A"  # fell back to default
    assert a.id  # silence unused warning


# ---------- API surface ----------


@pytest.mark.asyncio
async def test_platforms_status_returns_account_list(engine, db_session):
    from fastapi.testclient import TestClient

    from main import app

    await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    await _seed_token(db_session, platform="instagram", user_id="B", label="@b")
    await _seed_token(db_session, platform="threads", user_id="T", label="@t")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        r = c.get("/api/platforms/status")
        assert r.status_code == 200
        body = r.json()
        assert len(body["instagram"]["accounts"]) == 2
        assert len(body["threads"]["accounts"]) == 1
        assert body["tiktok"]["accounts"] == []
        ig_labels = sorted(a["account_label"] for a in body["instagram"]["accounts"])
        assert ig_labels == ["@a", "@b"]


@pytest.mark.asyncio
async def test_disconnect_requires_account_id_when_multiple(engine, db_session):
    from fastapi.testclient import TestClient

    from main import app

    await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    await _seed_token(db_session, platform="instagram", user_id="B", label="@b")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        # No account_id → 400 because there are 2.
        r = c.delete("/api/auth/instagram/disconnect")
        assert r.status_code == 400
        assert "disambiguate" in r.text.lower()


@pytest.mark.asyncio
async def test_disconnect_specific_account(engine, db_session, session_factory):
    from fastapi.testclient import TestClient

    from main import app

    a = await _seed_token(db_session, platform="instagram", user_id="A", label="@a")
    await _seed_token(db_session, platform="instagram", user_id="B", label="@b")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        r = c.delete(f"/api/auth/instagram/disconnect?account_id={a.id}")
        assert r.status_code == 200

    async with session_factory() as fresh:
        rows = (
            await fresh.execute(select(PlatformToken).where(PlatformToken.platform == "instagram"))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].user_id == "B"


@pytest.mark.asyncio
async def test_create_post_with_explicit_platform_account(engine, db_session, session_factory):
    from fastapi.testclient import TestClient

    from main import app

    a = await _seed_token(db_session, platform="threads", user_id="A", label="@a")
    b = await _seed_token(db_session, platform="threads", user_id="B", label="@b")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        payload = {
            "caption": "hello world",
            "platforms": ["threads"],
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "platform_accounts": {"threads": str(b.id)},
        }
        r = c.post("/api/posts", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["platform_accounts"]["threads"] == str(b.id)
        assert a.id  # silence unused warning


@pytest.mark.asyncio
async def test_create_post_rejects_account_id_on_non_targeted_platform(engine, db_session):
    from fastapi.testclient import TestClient

    from main import app

    b = await _seed_token(db_session, platform="instagram", user_id="B", label="@b")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        payload = {
            "caption": "hi",
            "platforms": ["threads"],  # NOT instagram
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "platform_accounts": {"instagram": str(b.id)},
        }
        r = c.post("/api/posts", json=payload)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_post_rejects_mismatched_platform_for_account(engine, db_session):
    from fastapi.testclient import TestClient

    from main import app

    ig = await _seed_token(db_session, platform="instagram", user_id="I", label="@ig")
    await db_session.close()

    with TestClient(app) as c:
        c.post("/api/auth/login", json={"password": "secret"})
        payload = {
            "caption": "hi",
            "platforms": ["threads"],
            "scheduled_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "platform_accounts": {"threads": str(ig.id)},  # IG account pinned for Threads
        }
        r = c.post("/api/posts", json=payload)
        assert r.status_code == 400
        assert "threads" in r.text.lower()
