"""Operator-facing alerts.

Sources of truth:
* PlatformToken rows → derive token-expiry alerts (F-27 fix)
* CircuitBreaker singleton → derive paused-platform alerts
* settings → derive missing-credentials alerts

`compute_alerts` is pure: given a list of PlatformToken rows + the current
breaker + the current settings + `now`, it returns a deterministic list of
alert dicts. The endpoint and the scheduler job both call it.

Email delivery is best-effort:
* If `settings.smtp_enabled` is false, `notify_via_email` is a no-op.
* Otherwise we send a short summary email via aiosmtplib (TLS on port 587 by
  default). Failures are logged but never raised — the dashboard is the
  primary surface; email is a courtesy.

De-dup state lives in this module's `_last_emailed` dict (in-memory).
A token must transition into the warning band, or be re-flagged after 24h,
to trigger a new email. A process restart resets the de-dup state — that is
acceptable for a personal-use scheduler.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from config import settings
from services.circuit_breaker import get_breaker

log = logging.getLogger("jack.alerts")

# How close to expiry triggers a warning. The spec says 7 days.
WARN_DAYS = 7
CRITICAL_DAYS = 2

_last_emailed: dict[str, datetime] = {}
_EMAIL_REPEAT_INTERVAL = timedelta(hours=24)

_SUPPORTED = ("threads", "instagram", "tiktok")


def _expires_in_days(expires_at: datetime | None, now: datetime) -> int | None:
    """Days remaining, rounded **up** so 'within 7 days' covers anything not
    yet 7 full 24h windows away. SQLite drops tz info on round-trip, so
    defensively assume UTC for naive datetimes."""
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    seconds = (expires_at - now).total_seconds()
    if seconds < 0:
        return int(seconds // 86400)  # negative; expired
    return max(int(math.ceil(seconds / 86400)), 1)


def compute_alerts(
    tokens: list[Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Derive the current operator-facing alerts from inputs that are cheap
    to gather. Idempotent and side-effect-free."""
    now = now or datetime.now(UTC)
    # D16: there can be more than one token per platform. Build a list per
    # platform so we can surface alerts for each account separately.
    by_platform: dict[str, list[Any]] = {}
    for t in tokens:
        by_platform.setdefault(t.platform, []).append(t)
    breaker = get_breaker()
    alerts: list[dict[str, Any]] = []

    # Auth / credential plumbing.
    if not settings.auth_enabled:
        alerts.append(
            {
                "key": "auth_disabled",
                "severity": "critical",
                "platform": None,
                "title": "Admin auth is not configured",
                "detail": "Set ADMIN_PASSWORD_HASH and SESSION_SECRET, then restart.",
            }
        )

    # Credential presence per platform.
    if not (settings.meta_app_id and settings.meta_app_secret):
        alerts.append(
            {
                "key": "meta_creds_missing",
                "severity": "warning",
                "platform": "instagram",
                "title": "Meta credentials missing",
                "detail": "Set META_APP_ID and META_APP_SECRET to enable Threads/Instagram.",
            }
        )
    if not (settings.tiktok_client_key and settings.tiktok_client_secret):
        alerts.append(
            {
                "key": "tiktok_creds_missing",
                "severity": "warning",
                "platform": "tiktok",
                "title": "TikTok credentials missing",
                "detail": "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET to enable TikTok.",
            }
        )

    # Per-platform token + breaker checks. With D16, breaker is platform-wide
    # but token expiry alerts are per-account (so each account that's
    # expiring shows up).
    for platform in _SUPPORTED:
        accounts = by_platform.get(platform) or []
        if not accounts:
            continue

        # Breaker open — emit once per platform regardless of how many
        # accounts are connected, since the breaker is platform-wide.
        if breaker.is_open(platform, now=now):
            status = breaker.status(platform, now=now)
            alerts.append(
                {
                    "key": f"{platform}_paused",
                    "severity": "warning",
                    "platform": platform,
                    "title": f"{platform.capitalize()} is paused (circuit breaker open)",
                    "detail": (
                        f"{status['consecutive_failures']} consecutive failures. "
                        f"Paused until {status['paused_until']}. Last error: {status['last_error']}"
                    ),
                }
            )

        for token in accounts:
            label = token.account_label or token.user_id or "(unknown)"
            scope = f"{platform}:{token.id}"
            # Expiry awareness (F-27).
            if token.expires_at is None:
                alerts.append(
                    {
                        "key": f"{scope}_expires_unknown",
                        "severity": "warning",
                        "platform": platform,
                        "title": f"{platform.capitalize()} account {label} has no expiry",
                        "detail": (
                            "The platform did not return expires_in when the token was issued, "
                            "so the scheduler cannot refresh it proactively. Reconnect this "
                            "account to mint a fresh token."
                        ),
                    }
                )
                continue

            days = _expires_in_days(token.expires_at, now)
            if days is None:
                continue
            if days < 0:
                alerts.append(
                    {
                        "key": f"{scope}_expired",
                        "severity": "critical",
                        "platform": platform,
                        "title": f"{platform.capitalize()} account {label} expired",
                        "detail": (
                            f"The {platform} access token for {label} expired on "
                            f"{token.expires_at.isoformat()}. Reconnect to keep publishing."
                        ),
                    }
                )
            elif days <= CRITICAL_DAYS:
                alerts.append(
                    {
                        "key": f"{scope}_expiring_critical",
                        "severity": "critical",
                        "platform": platform,
                        "title": (
                            f"{platform.capitalize()} account {label} expires in {days} day(s)"
                        ),
                        "detail": (
                            "The scheduler is attempting to refresh automatically, "
                            "but if refresh has been failing you should reconnect now."
                        ),
                    }
                )
            elif days <= WARN_DAYS:
                alerts.append(
                    {
                        "key": f"{scope}_expiring_soon",
                        "severity": "warning",
                        "platform": platform,
                        "title": (
                            f"{platform.capitalize()} account {label} expires in {days} days"
                        ),
                        "detail": (
                            "Automatic refresh will run within 48h of expiry. "
                            "No action required unless you see refresh failures."
                        ),
                    }
                )

    return alerts


async def notify_via_email(alerts: list[dict[str, Any]], *, now: datetime | None = None) -> int:
    """Send a single digest email summarising critical/warning alerts.

    Returns the number of alerts included in the email. Returns 0 if SMTP is
    not configured, if there are no actionable alerts, or if every alert was
    emailed in the last 24h.
    """
    now = now or datetime.now(UTC)
    if not settings.smtp_enabled:
        return 0
    if not alerts:
        return 0

    # De-dup: only include alerts not emailed in the last 24h.
    fresh = []
    for a in alerts:
        last = _last_emailed.get(a["key"])
        if last is None or (now - last) >= _EMAIL_REPEAT_INTERVAL:
            fresh.append(a)
    if not fresh:
        return 0

    subject_severity = "CRITICAL" if any(a["severity"] == "critical" for a in fresh) else "WARNING"
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = settings.alert_email
    msg["Subject"] = f"[JACK Scheduler] {subject_severity}: {len(fresh)} alert(s)"
    body_lines = [f"Alerts as of {now.isoformat()}:", ""]
    for a in fresh:
        body_lines.append(f"- [{a['severity'].upper()}] {a['title']}")
        body_lines.append(f"    {a['detail']}")
        body_lines.append("")
    body_lines.append(f"Dashboard: {settings.base_url}/")
    msg.set_content("\n".join(body_lines))

    try:
        import aiosmtplib

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_pass,
            start_tls=True,
        )
        for a in fresh:
            _last_emailed[a["key"]] = now
        log.info("Sent alert email with %d alert(s) to %s", len(fresh), settings.alert_email)
        return len(fresh)
    except Exception:
        log.exception("Failed to send alert email")
        return 0


def _reset_dedup_state_for_tests() -> None:
    """Helper for tests; not part of the public API."""
    _last_emailed.clear()
