"""Per-platform overrides (Deliverable 4).

Covers:
* Schema validation: per-platform caption + hashtag limits, override keys must
  match selected platforms, override caption replaces base at validation time.
* Persistence: overrides land in scheduled_posts.platform_overrides.
* Scheduler: _effective() honors override caption / media.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import scheduler
from schemas import (
    PLATFORM_CAPTION_LIMITS,
    PLATFORM_HASHTAG_LIMITS,
    ScheduledPostCreate,
)
from tests.conftest import make_post


def _future_dt():
    return datetime.now(UTC) + timedelta(hours=1)


def test_base_caption_too_long_for_threads_is_rejected():
    long_caption = "x" * (PLATFORM_CAPTION_LIMITS["threads"] + 1)
    with pytest.raises(ValueError):
        ScheduledPostCreate(
            caption=long_caption,
            platforms=["threads"],
            scheduled_at=_future_dt(),
        )


def test_override_caption_passes_when_short_even_if_base_long():
    # Base too long for threads, but override is short.
    base = "x" * (PLATFORM_CAPTION_LIMITS["threads"] + 100)
    payload = ScheduledPostCreate(
        caption=base,
        platforms=["instagram", "threads"],
        media_url="https://x/y.jpg",
        media_type="IMAGE",
        scheduled_at=_future_dt(),
        platform_overrides={"threads": {"caption": "tight thread post"}},
    )
    assert payload.platform_overrides["threads"].caption == "tight thread post"


def test_too_many_hashtags_rejected_per_platform():
    tags = " ".join(f"#tag{i}" for i in range(PLATFORM_HASHTAG_LIMITS["instagram"] + 1))
    with pytest.raises(ValueError) as exc:
        ScheduledPostCreate(
            caption=f"hi {tags}",
            platforms=["instagram"],
            media_url="https://x/y.jpg",
            media_type="IMAGE",
            scheduled_at=_future_dt(),
        )
    assert "hashtag" in str(exc.value).lower()


def test_override_for_unselected_platform_is_rejected():
    with pytest.raises(ValueError):
        ScheduledPostCreate(
            caption="hi",
            platforms=["threads"],
            scheduled_at=_future_dt(),
            platform_overrides={"instagram": {"caption": "x"}},
        )


def test_unknown_override_platform_is_rejected():
    with pytest.raises(ValueError):
        ScheduledPostCreate(
            caption="hi",
            platforms=["threads"],
            scheduled_at=_future_dt(),
            platform_overrides={"linkedin": {"caption": "x"}},
        )


def test_empty_override_dropped_at_validation():
    payload = ScheduledPostCreate(
        caption="hi",
        platforms=["threads"],
        scheduled_at=_future_dt(),
        platform_overrides={"threads": {}},
    )
    assert payload.platform_overrides == {}


@pytest.mark.asyncio
async def test_scheduler_uses_override_caption(seeded_tokens, db_session):
    """The scheduler must hand the override caption to the platform publisher."""
    post = make_post(
        caption="BASE",
        platforms=("threads",),
        platform_results={},
    )
    # The model attribute is a dict, not a Pydantic block — that is the on-disk shape.
    post.platform_overrides = {"threads": {"caption": "OVERRIDDEN"}}
    db_session.add(post)
    await db_session.commit()

    seen = {"caption": None}

    async def fake_threads(*, access_token, user_id, caption, media_url, media_type):
        seen["caption"] = caption
        return "thr-1"

    import services.meta as meta

    meta.publish_to_threads = fake_threads

    from models import PlatformToken

    token_rows = (await db_session.execute(select(PlatformToken))).scalars().all()
    tokens = scheduler._build_token_index(token_rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen["caption"] == "OVERRIDDEN"


@pytest.mark.asyncio
async def test_scheduler_falls_back_to_base_when_no_override(seeded_tokens, db_session):
    post = make_post(caption="BASE", platforms=("threads",))
    db_session.add(post)
    await db_session.commit()

    seen = {"caption": None}

    async def fake_threads(*, access_token, user_id, caption, media_url, media_type):
        seen["caption"] = caption
        return "thr-1"

    import services.meta as meta

    meta.publish_to_threads = fake_threads

    from models import PlatformToken

    token_rows = (await db_session.execute(select(PlatformToken))).scalars().all()
    tokens = scheduler._build_token_index(token_rows)
    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()

    assert seen["caption"] == "BASE"


@pytest.mark.asyncio
async def test_create_post_persists_overrides(engine):
    """End-to-end: POST /api/posts with overrides — DB row carries the overrides."""
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"password": "secret"})
        assert r.status_code == 200, r.text

        payload = {
            "caption": "base post",
            "platforms": ["threads", "instagram"],
            "media_url": "https://x/y.jpg",
            "media_type": "IMAGE",
            "scheduled_at": _future_dt().isoformat(),
            "platform_overrides": {
                "threads": {"caption": "thread version"},
                "instagram": {"caption": "ig version"},
            },
        }
        r = c.post("/api/posts", json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["platform_overrides"]["threads"]["caption"] == "thread version"
        assert body["platform_overrides"]["instagram"]["caption"] == "ig version"
