"""Scheduler safety tests for Deliverable 2.

Covers the F-21 fix (do not republish already-succeeded platforms), the
circuit-breaker integration (defer + re-pend), and the reconcile path.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

import scheduler
from models import PostStatus
from tests.conftest import make_post


async def _run_publish(db_session, post, *, fake_publishers, breaker_failures=None):
    """Drive _publish_one with controlled fake platform calls."""
    db_session.add(post)
    await db_session.commit()

    # Monkeypatch the platform publishers inside scheduler._publish_platform.
    import services.meta as meta
    import services.tiktok as tt

    meta.publish_to_threads = fake_publishers["threads"]
    meta.publish_to_instagram = fake_publishers["instagram"]
    tt.upload_to_tiktok_drafts = fake_publishers["tiktok"]

    # Build a tokens dict matching seeded_tokens fixture content.
    from models import PlatformToken

    token_rows = (await db_session.execute(select(PlatformToken))).scalars().all()
    tokens = scheduler._build_token_index(token_rows)

    await scheduler._publish_one(db_session, post, tokens)
    await db_session.commit()
    return post


def _ok_threads(**_):
    return "threads-post-id-1"


def _ok_instagram(**_):
    return "ig-post-id-1"


def _ok_tiktok(**_):
    return "tt-publish-id-1"


async def _async_ok_threads(**_):
    return "threads-post-id-1"


async def _async_ok_instagram(**_):
    return "ig-post-id-1"


async def _async_ok_tiktok(**_):
    return "tt-publish-id-1"


async def _async_fail_threads(**_):
    raise RuntimeError("threads is down")


@pytest.mark.asyncio
async def test_retry_skips_already_succeeded_platforms(seeded_tokens, db_session):
    """F-21: a failed post with a prior success on Threads must not republish Threads."""
    post = make_post(
        platforms=("threads", "instagram"),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
        platform_results={
            "threads": {"success": True, "post_id": "previous-threads-id"},
            "instagram": {"success": False, "error": "earlier error"},
        },
    )

    threads_calls = {"n": 0}
    ig_calls = {"n": 0}

    async def fake_threads(**_):
        threads_calls["n"] += 1
        return "would-be-duplicate"

    async def fake_ig(**_):
        ig_calls["n"] += 1
        return "ig-post-id-1"

    await _run_publish(
        db_session,
        post,
        fake_publishers={
            "threads": fake_threads,
            "instagram": fake_ig,
            "tiktok": _async_ok_tiktok,
        },
    )

    assert threads_calls["n"] == 0, "Threads must NOT be republished — F-21 regression."
    assert ig_calls["n"] == 1
    assert post.platform_results["threads"]["post_id"] == "previous-threads-id"
    assert post.platform_results["instagram"]["success"] is True
    assert post.status == PostStatus.published


@pytest.mark.asyncio
async def test_circuit_breaker_defers_publish(seeded_tokens, db_session):
    """When the breaker is open for a platform, the post stays pending."""
    from services.circuit_breaker import get_breaker

    breaker = get_breaker()
    # Force the breaker open for threads.
    for _ in range(breaker.threshold):
        breaker.record_failure("threads", "x")
    assert breaker.is_open("threads") is True

    post = make_post(platforms=("threads",))
    threads_calls = {"n": 0}

    async def fake_threads(**_):
        threads_calls["n"] += 1
        return "should-not-be-called"

    await _run_publish(
        db_session,
        post,
        fake_publishers={
            "threads": fake_threads,
            "instagram": _async_ok_instagram,
            "tiktok": _async_ok_tiktok,
        },
    )

    assert threads_calls["n"] == 0
    assert post.status == PostStatus.pending  # re-pended for next sweep
    assert post.platform_results["threads"]["deferred"] is True
    assert post.platform_results["threads"]["paused_until"] is not None


@pytest.mark.asyncio
async def test_breaker_trips_after_threshold_failures(seeded_tokens, db_session):
    """Three failures across three posts trips the breaker on the third."""
    from services.circuit_breaker import get_breaker

    breaker = get_breaker()

    for i in range(breaker.threshold):
        post = make_post(platforms=("threads",), caption=f"#{i}")
        await _run_publish(
            db_session,
            post,
            fake_publishers={
                "threads": _async_fail_threads,
                "instagram": _async_ok_instagram,
                "tiktok": _async_ok_tiktok,
            },
        )

    assert breaker.is_open("threads") is True
    # A fourth post should be deferred, not attempted.
    fourth = make_post(platforms=("threads",), caption="fourth")
    calls = {"n": 0}

    async def trap_threads(**_):
        calls["n"] += 1
        return "no"

    await _run_publish(
        db_session,
        fourth,
        fake_publishers={
            "threads": trap_threads,
            "instagram": _async_ok_instagram,
            "tiktok": _async_ok_tiktok,
        },
    )
    assert calls["n"] == 0
    assert fourth.status == PostStatus.pending


@pytest.mark.asyncio
async def test_success_resets_breaker_failure_count(seeded_tokens, db_session):
    """A success in the middle of a failure streak must reset the counter."""
    from services.circuit_breaker import get_breaker

    breaker = get_breaker()
    # Two failures.
    for _ in range(2):
        post = make_post(platforms=("threads",))
        await _run_publish(
            db_session,
            post,
            fake_publishers={
                "threads": _async_fail_threads,
                "instagram": _async_ok_instagram,
                "tiktok": _async_ok_tiktok,
            },
        )
    assert breaker.status("threads")["consecutive_failures"] == 2

    # Now a success.
    post = make_post(platforms=("threads",))
    await _run_publish(
        db_session,
        post,
        fake_publishers={
            "threads": _async_ok_threads,
            "instagram": _async_ok_instagram,
            "tiktok": _async_ok_tiktok,
        },
    )
    assert breaker.status("threads")["consecutive_failures"] == 0
    assert breaker.is_open("threads") is False


@pytest.mark.asyncio
async def test_partial_success_marks_failed_not_published(seeded_tokens, db_session):
    """If threads succeeds but instagram fails (and no defer), status=failed."""
    post = make_post(
        platforms=("threads", "instagram"),
        media_url="https://x/y.jpg",
        media_type="IMAGE",
    )

    async def fake_threads(**_):
        return "threads-ok"

    async def fake_ig(**_):
        raise RuntimeError("ig is broken")

    await _run_publish(
        db_session,
        post,
        fake_publishers={
            "threads": fake_threads,
            "instagram": fake_ig,
            "tiktok": _async_ok_tiktok,
        },
    )
    assert post.status == PostStatus.failed
    assert post.platform_results["threads"]["success"] is True
    assert post.platform_results["instagram"]["success"] is False
    assert post.last_error and "instagram" in post.last_error
