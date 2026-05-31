"""APScheduler entry points + the publish loop.

Safety model (audit IDs F-20, F-21, F-30, F-34):

* `run_due_posts` uses a single `UPDATE ... RETURNING` to atomically claim
  any pending posts whose scheduled_at is in the past. Postgres row locks
  guarantee that a concurrent tick cannot claim the same row twice.
* `_publish_one` skips any platform whose `platform_results[p].success` is
  already truthy. This is the F-21 fix: a retry on a partially-succeeded
  post no longer re-publishes the platforms that already succeeded.
* Per-platform circuit breaker (services/circuit_breaker.py). After N
  consecutive failures a platform is paused for a cool-down window. While
  paused the scheduler records the skip in `platform_results` with a
  `paused_until` marker; the post stays `pending` for the next sweep.
* `reconcile_processing_posts` runs once at boot to find any rows left in
  `processing` from a crashed previous run (F-30) and either resumes them
  or, when there is enough context, completes them. Crash recovery is
  expanded in Deliverable 9.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import and_, select, update

from config import settings
from services.circuit_breaker import get_breaker

log = logging.getLogger("jack.scheduler")

_scheduler = AsyncIOScheduler(
    executors={"default": AsyncIOExecutor()},
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60},
    timezone="UTC",
)


def start_scheduler() -> None:
    _scheduler.add_job(
        run_due_posts,
        trigger=IntervalTrigger(seconds=settings.scheduler_interval_seconds),
        id="publish_scheduler",
        replace_existing=True,
        name="Drain due scheduled posts",
    )
    _scheduler.add_job(
        refresh_expiring_tokens,
        trigger=IntervalTrigger(hours=6),
        id="token_refresher",
        replace_existing=True,
        name="Refresh tokens approaching expiry",
    )
    _scheduler.add_job(
        token_expiry_check,
        trigger=IntervalTrigger(hours=24),
        id="token_expiry_alerts",
        replace_existing=True,
        name="Surface token-expiry warnings to operator + optional email",
    )
    if settings.substack_feed_list:
        _scheduler.add_job(
            poll_substack_feeds,
            trigger=IntervalTrigger(minutes=15),
            id="substack_poller",
            replace_existing=True,
            name="Poll Substack RSS feeds for new posts",
        )
    _scheduler.start()
    log.info("Scheduler started (interval=%ss)", settings.scheduler_interval_seconds)


def shutdown_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def get_scheduler() -> AsyncIOScheduler:
    """Exposed for tests + Deliverable 8 (analytics jobs) to schedule follow-ups."""
    return _scheduler


# ---------------------------------------------------------------------------
# The drain loop
# ---------------------------------------------------------------------------


async def run_due_posts() -> None:
    """Atomically claim due posts and publish them. Safe against overlapping runs."""
    from database import AsyncSessionLocal
    from models import PlatformToken, PostStatus, ScheduledPost

    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        claim = (
            update(ScheduledPost)
            .where(
                and_(
                    ScheduledPost.status == PostStatus.pending,
                    ScheduledPost.scheduled_at <= now,
                )
            )
            .values(status=PostStatus.processing, updated_at=now)
            .returning(ScheduledPost.id)
        )
        claimed = (await db.execute(claim)).scalars().all()
        await db.commit()

        if not claimed:
            return

        result = await db.execute(
            select(ScheduledPost).where(ScheduledPost.id.in_(claimed))
        )
        posts = result.scalars().all()

        token_rows = (
            await db.execute(select(PlatformToken).order_by(PlatformToken.created_at))
        ).scalars().all()
        tokens = _build_token_index(token_rows)

        for post in posts:
            await _publish_one(db, post, tokens)

        await db.commit()


def _build_token_index(token_rows) -> dict[str, Any]:
    """Build a multi-account-aware token index.

    Returns a dict shaped:
      {
        "by_id":           {<token-uuid-str>: PlatformToken, ...},
        "by_platform":     {"threads": [PlatformToken, ...], ...},  # ordered by created_at
      }

    The scheduler resolves per-(post, platform) tokens via `_resolve_token`
    below, which uses `post.platform_accounts.get(platform)` when set and
    falls back to the first-created account otherwise.
    """
    by_id: dict[str, Any] = {str(t.id): t for t in token_rows}
    by_platform: dict[str, list[Any]] = {}
    for t in token_rows:
        by_platform.setdefault(t.platform, []).append(t)
    return {"by_id": by_id, "by_platform": by_platform}


def _resolve_token(post, platform: str, tokens: dict[str, Any]):
    """Return the PlatformToken to use for this (post, platform), or None
    if nothing is connected for that platform."""
    accounts = (post.platform_accounts or {}) if hasattr(post, "platform_accounts") else {}
    pinned = accounts.get(platform)
    if pinned:
        tok = tokens["by_id"].get(str(pinned))
        if tok is not None:
            return tok
        # Pinned account was deleted; fall through to the default for the platform.
        log.warning(
            "Post=%s pinned %s account %s but it no longer exists; "
            "falling back to default account.",
            post.id,
            platform,
            pinned,
        )
    candidates = tokens["by_platform"].get(platform) or []
    return candidates[0] if candidates else None


async def _publish_one(
    db,
    post,
    tokens: dict[str, Any],
) -> None:
    """Publish one post across every platform it targets.

    Semantics (matches the audit fixes):
    * skip platforms whose prior result was a success (F-21)
    * skip platforms whose circuit breaker is open and leave the post pending
      so the next sweep retries them (F-20)
    * record every per-platform outcome in `platform_results`
    * post is `published` iff every targeted platform has a success result
    * post is `pending` again if any platform was deferred by the breaker
    * post is `failed` only when at least one platform errored AND no platform
      was deferred (so the operator can retry without ambiguity)
    """
    from models import PostStatus

    breaker = get_breaker()
    results: dict[str, dict[str, Any]] = dict(post.platform_results or {})
    attempts: list[dict[str, Any]] = list(post.attempts or [])
    saw_failure = False
    saw_defer = False
    last_error: str | None = None

    for platform in post.platforms:
        prior = results.get(platform) or {}
        if prior.get("success") is True:
            # Idempotency: this platform was already published in a previous
            # attempt. Do not republish (F-21).
            log.info(
                "Skipping %s for post=%s (already published as %s)",
                platform,
                post.id,
                prior.get("post_id") or prior.get("publish_id"),
            )
            continue

        if breaker.is_open(platform):
            # Defer until the breaker resets.
            status = breaker.status(platform)
            results[platform] = {
                "success": False,
                "deferred": True,
                "paused_until": status["paused_until"],
                "error": "Circuit breaker open — platform paused.",
            }
            saw_defer = True
            log.warning(
                "Deferring %s for post=%s — breaker open until %s",
                platform,
                post.id,
                status["paused_until"],
            )
            continue

        # F-79: mark this platform "in flight" and commit BEFORE calling out
        # to the network. If we crash mid-publish, reconcile_processing_posts
        # will see the in_flight marker and surface it to the operator instead
        # of silently dropping or silently retrying.
        results[platform] = {
            "in_flight": True,
            "started_at": datetime.now(UTC).isoformat(),
        }
        post.platform_results = dict(results)
        await db.commit()

        # Resolve which account this publish will use so we can hand the same
        # account to the analytics pull-back jobs (D16).
        chosen_token = _resolve_token(post, platform, tokens)
        chosen_account_id = chosen_token.id if chosen_token is not None else None

        try:
            await _publish_platform(db, post, platform, tokens, results)
            breaker.record_success(platform)
            _schedule_analytics_if_external(
                post.id, platform, results.get(platform), account_id=chosen_account_id
            )
        except Exception as exc:
            err = f"{platform}: {exc}"
            log.exception("Publish failed for post=%s platform=%s", post.id, platform)
            results[platform] = {"success": False, "error": str(exc)}
            saw_failure = True
            last_error = err
            tripped = breaker.record_failure(platform, str(exc))
            if tripped:
                log.error(
                    "Circuit breaker tripped on %s after %s consecutive failures; "
                    "platform paused for %ss.",
                    platform,
                    breaker.threshold,
                    breaker.cooldown_seconds,
                )
        # Persist per-platform progress after each platform regardless of
        # outcome — that's the F-79 fix.
        post.platform_results = dict(results)
        await db.commit()

    attempts.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "results": {k: bool(v.get("success")) for k, v in results.items()},
        }
    )

    post.platform_results = results
    post.attempts = attempts
    post.last_error = last_error if saw_failure else None
    post.updated_at = datetime.now(UTC)

    targeted = set(post.platforms)
    all_success = all((results.get(p) or {}).get("success") is True for p in targeted)

    if all_success:
        post.status = PostStatus.published
    elif saw_defer and not saw_failure:
        # Pure defer: nothing failed, breaker just held us back. Re-pend.
        post.status = PostStatus.pending
    else:
        # Mixed defer + failure → mark failed so the operator sees the error;
        # the deferred platforms still carry the paused_until marker so retry
        # logic can DTRT.
        post.status = PostStatus.failed


def _schedule_analytics_if_external(
    post_id,
    platform: str,
    results_block: dict | None,
    *,
    account_id=None,
) -> None:
    """Look at a per-platform result block; if it has a usable external id,
    queue the +1h/+24h/+72h/+7d analytics pull-backs.

    `account_id` (D16) pins the specific PlatformToken used at publish time so
    the sampling jobs query metrics from the same account.
    """
    if not results_block or not results_block.get("success"):
        return
    external = results_block.get("post_id") or results_block.get("publish_id")
    if not external:
        return
    try:
        from services.analytics import schedule_analytics_pulls

        schedule_analytics_pulls(post_id, platform, external, account_id=account_id)
    except Exception:
        log.exception("Could not queue analytics pulls for post=%s platform=%s", post_id, platform)


def _effective(post, platform: str) -> tuple[str, str | None, str | None]:
    """Return (caption, media_url, media_type) honoring per-platform overrides."""
    overrides = (post.platform_overrides or {}).get(platform) or {}
    caption = overrides.get("caption") or post.caption
    media_url = overrides.get("media_url") if overrides.get("media_url") else post.media_url
    media_type = overrides.get("media_type") if overrides.get("media_type") else post.media_type
    return caption, media_url, media_type


async def _publish_platform(
    db,
    post,
    platform: str,
    tokens: dict[str, Any],
    results: dict[str, dict[str, Any]],
) -> None:
    """Single-platform publish. Raises on failure."""
    from services.meta import publish_to_instagram, publish_to_threads
    from services.tiktok import ensure_fresh_tiktok_token, upload_to_tiktok_drafts

    token = _resolve_token(post, platform, tokens)
    if token is None:
        raise RuntimeError(f"{platform} is not connected")

    caption, media_url, media_type = _effective(post, platform)

    if platform == "threads":
        external_id = await publish_to_threads(
            access_token=token.access_token,
            user_id=token.user_id,
            caption=caption,
            media_url=media_url,
            media_type=media_type,
        )
        results[platform] = {"success": True, "post_id": external_id}
        return

    if platform == "instagram":
        external_id = await publish_to_instagram(
            access_token=token.access_token,
            ig_user_id=token.user_id,
            caption=caption,
            media_url=media_url,
            media_type=media_type,
        )
        results[platform] = {"success": True, "post_id": external_id}
        return

    if platform == "tiktok":
        if not media_url:
            raise RuntimeError("TikTok requires media_url (video).")
        fresh = await ensure_fresh_tiktok_token(db, token)
        publish_id = await upload_to_tiktok_drafts(
            access_token=fresh.access_token,
            caption=caption,
            video_url=media_url,
        )
        results[platform] = {
            "success": True,
            "publish_id": publish_id,
            "note": "Uploaded to drafts; open the TikTok app to publish. Caption is set in-app.",
        }
        return

    raise RuntimeError(f"Unknown platform: {platform}")


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


async def poll_substack_feeds() -> None:
    """Scheduler job wrapper around services.substack.poll_all_feeds (D5)."""
    from services.substack import poll_all_feeds

    try:
        created = await poll_all_feeds()
        if created:
            log.info("Substack poller created %d draft(s).", created)
    except Exception:
        log.exception("Substack poller failed")


async def token_expiry_check() -> None:
    """Daily job: compute current alerts, log warnings, optionally email.

    The dashboard reads the same `compute_alerts` function on demand via
    `/api/admin/alerts`, so this job exists primarily to push email when SMTP
    is configured. Logging is best-effort visibility for operators who tail
    Replit's console.
    """
    from database import AsyncSessionLocal
    from models import PlatformToken
    from services.alerts import compute_alerts, notify_via_email

    async with AsyncSessionLocal() as db:
        tokens = (await db.execute(select(PlatformToken))).scalars().all()
    alerts = compute_alerts(list(tokens))
    if not alerts:
        log.info("token_expiry_check: no alerts.")
        return
    for a in alerts:
        log.warning(
            "ALERT [%s] %s: %s",
            a["severity"].upper(),
            a["title"],
            a["detail"],
        )
    sent = await notify_via_email(alerts)
    if sent:
        log.info("token_expiry_check: emailed %d alert(s).", sent)


async def refresh_expiring_tokens() -> None:
    """Refresh tokens within 48h of expiry. Meta tokens use long-lived re-exchange;
    TikTok uses its refresh_token grant."""
    from database import AsyncSessionLocal
    from models import PlatformToken
    from services.meta import refresh_long_lived_token
    from services.tiktok import ensure_fresh_tiktok_token

    horizon = datetime.now(UTC).timestamp() + (48 * 3600)

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PlatformToken))).scalars().all()
        for token in rows:
            if token.expires_at is None:
                # F-27: surface this rather than silently skip. Deliverable 3
                # extends this with an actual alert; for now log loudly.
                log.warning(
                    "Token for %s has no expires_at; cannot proactively refresh.",
                    token.platform,
                )
                continue
            if token.expires_at.timestamp() > horizon:
                continue
            try:
                if token.platform in ("threads", "instagram"):
                    await refresh_long_lived_token(db, token)
                elif token.platform == "tiktok":
                    await ensure_fresh_tiktok_token(db, token, force=True)
                log.info("Refreshed %s token", token.platform)
            except Exception:
                log.exception("Token refresh failed for %s", token.platform)
        await db.commit()


# ---------------------------------------------------------------------------
# publish-now (manual trigger)
# ---------------------------------------------------------------------------


async def publish_now(post_id) -> None:
    """Manually trigger a post immediately. Used by the publish-now endpoint."""
    from database import AsyncSessionLocal
    from models import PlatformToken, PostStatus, ScheduledPost

    async with AsyncSessionLocal() as db:
        claim = (
            update(ScheduledPost)
            .where(
                and_(
                    ScheduledPost.id == post_id,
                    ScheduledPost.status == PostStatus.pending,
                )
            )
            .values(status=PostStatus.processing, updated_at=datetime.now(UTC))
            .returning(ScheduledPost.id)
        )
        if not (await db.execute(claim)).scalars().first():
            # Already claimed by the periodic tick (F-32) or not pending. Nothing to do.
            await db.commit()
            return
        await db.commit()

        post = (
            await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
        ).scalar_one()
        token_rows = (
            await db.execute(select(PlatformToken).order_by(PlatformToken.created_at))
        ).scalars().all()
        tokens = _build_token_index(token_rows)

        await _publish_one(db, post, tokens)
        await db.commit()


# ---------------------------------------------------------------------------
# Crash recovery — seed for Deliverable 9
# ---------------------------------------------------------------------------


async def reconcile_processing_posts() -> None:
    """Boot-time reconcile of any post left in `processing` from a crashed run.

    Decision tree (per audit IDs F-30, F-79):

    1. For each platform with an `in_flight: True` marker (set just before the
       network call), we don't know if the platform accepted the publish — the
       process died mid-flight. Conservative path: mark that platform as failed
       with a clear "verify manually" message and leave the post in `failed`
       state so the operator can decide. (Aggressive auto-retry could
       double-publish, since the platforms do not all give us an idempotency
       key we could use to dedupe.)

    2. After clearing in_flight markers, the rest of the logic is unchanged:
       * all targeted platforms have success → `published`
       * partial success → `failed` (retry will skip already-succeeded ones)
       * no platforms succeeded → re-queue as `pending`
    """
    from database import AsyncSessionLocal
    from models import PostStatus, ScheduledPost

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(ScheduledPost).where(ScheduledPost.status == PostStatus.processing)
            )
        ).scalars().all()
        if not rows:
            return

        log.warning("Crash recovery: found %d post(s) stuck in processing.", len(rows))
        for post in rows:
            now = datetime.now(UTC)
            targeted = set(post.platforms)
            results = dict(post.platform_results or {})

            # Step 1: resolve in-flight markers (F-79).
            in_flight_platforms = [
                p for p, v in results.items() if isinstance(v, dict) and v.get("in_flight")
            ]
            for p in in_flight_platforms:
                results[p] = {
                    "success": False,
                    "error": (
                        "In-flight at crash. Could not confirm whether the platform accepted "
                        "the publish. Verify manually in the platform UI before retrying."
                    ),
                    "needs_manual_verify": True,
                    "started_at": results[p].get("started_at"),
                }
                log.warning(
                    "Crash recovery: post=%s platform=%s was in-flight; flagged needs_manual_verify.",
                    post.id,
                    p,
                )

            successes = {p for p in targeted if (results.get(p) or {}).get("success") is True}
            needs_verify = any(
                (results.get(p) or {}).get("needs_manual_verify") for p in results
            )

            if successes == targeted:
                post.status = PostStatus.published
                post.last_error = None
                log.info("Crash recovery: post=%s marked published (all platforms had success).", post.id)
            elif needs_verify:
                post.status = PostStatus.failed
                post.last_error = (
                    "Crash during publish — one or more platforms were in-flight. "
                    "Verify the platform UI before hitting Retry."
                )
                log.info(
                    "Crash recovery: post=%s marked failed with needs_manual_verify.",
                    post.id,
                )
            elif successes:
                post.status = PostStatus.failed
                post.last_error = (
                    f"Crash during publish — succeeded on {sorted(successes)}; "
                    "retry will skip those platforms."
                )
                log.info("Crash recovery: post=%s marked failed for partial success.", post.id)
            else:
                post.status = PostStatus.pending
                post.scheduled_at = now
                log.info("Crash recovery: post=%s re-queued (no platforms had succeeded).", post.id)

            post.platform_results = results
            post.updated_at = now
        await db.commit()


# Exposed for testing
async def _sleep(seconds: float) -> None:  # pragma: no cover
    await asyncio.sleep(seconds)
