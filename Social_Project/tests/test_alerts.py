"""Alert computation + email de-dup tests (Deliverable 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from services.alerts import (
    CRITICAL_DAYS,
    WARN_DAYS,
    _reset_dedup_state_for_tests,
    compute_alerts,
    notify_via_email,
)


class FakeToken:
    _counter = 0

    def __init__(self, platform: str, expires_in_days: float | None, label: str | None = None):
        FakeToken._counter += 1
        self.id = f"fake-{FakeToken._counter}"
        self.platform = platform
        self.user_id = f"user-{FakeToken._counter}"
        self.account_label = label or f"@{platform}-{FakeToken._counter}"
        if expires_in_days is None:
            self.expires_at = None
        else:
            self.expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)


@pytest.fixture(autouse=True)
def _reset():
    _reset_dedup_state_for_tests()
    yield
    _reset_dedup_state_for_tests()


def test_no_alerts_when_everything_is_healthy():
    tokens = [
        FakeToken("threads", 30),
        FakeToken("instagram", 30),
        FakeToken("tiktok", 30),
    ]
    alerts = compute_alerts(tokens)
    # `auth_disabled` and `creds_missing` only fire when settings are missing;
    # with the test conftest env they should be configured.
    keys = {a["key"] for a in alerts}
    assert "auth_disabled" not in keys
    assert "meta_creds_missing" not in keys
    assert "tiktok_creds_missing" not in keys
    # No expiry alerts for tokens 30 days out.
    for a in alerts:
        assert "_expiring" not in a["key"] and "_expired" not in a["key"]


def _matching(keys, platform: str, suffix: str) -> str | None:
    """Find an alert key shaped `<platform>:<id>_<suffix>` (D16 per-account scope)."""
    for k in keys:
        if k.startswith(f"{platform}:") and k.endswith(f"_{suffix}"):
            return k
    return None


def test_warning_at_seven_days():
    tokens = [FakeToken("threads", WARN_DAYS - 1)]
    by_key = {a["key"]: a for a in compute_alerts(tokens)}
    match = _matching(by_key.keys(), "threads", "expiring_soon")
    assert match is not None
    assert by_key[match]["severity"] == "warning"


def test_critical_at_two_days():
    tokens = [FakeToken("threads", CRITICAL_DAYS - 1)]
    by_key = {a["key"]: a for a in compute_alerts(tokens)}
    match = _matching(by_key.keys(), "threads", "expiring_critical")
    assert match is not None
    assert by_key[match]["severity"] == "critical"


def test_expired_token_emits_critical():
    tokens = [FakeToken("tiktok", -1)]
    by_key = {a["key"]: a for a in compute_alerts(tokens)}
    match = _matching(by_key.keys(), "tiktok", "expired")
    assert match is not None
    assert by_key[match]["severity"] == "critical"


def test_token_without_expiry_emits_warning_f27():
    tokens = [FakeToken("instagram", None)]
    keys = {a["key"] for a in compute_alerts(tokens)}
    assert _matching(keys, "instagram", "expires_unknown") is not None


def test_paused_platform_alert():
    from services.circuit_breaker import get_breaker

    cb = get_breaker()
    for _ in range(cb.threshold):
        cb.record_failure("threads", "boom")
    tokens = [FakeToken("threads", 30)]
    keys = {a["key"] for a in compute_alerts(tokens)}
    assert "threads_paused" in keys


@pytest.mark.asyncio
async def test_email_noop_when_smtp_disabled():
    from config import settings

    assert settings.smtp_enabled is False  # test env does not configure SMTP
    sent = await notify_via_email([{"key": "x", "severity": "warning", "title": "t", "detail": "d"}])
    assert sent == 0


@pytest.mark.asyncio
async def test_email_sends_when_smtp_configured_and_dedups_within_24h(monkeypatch):
    """Patch settings + aiosmtplib.send so we can verify the send path."""
    import services.alerts as alerts_mod

    fake_settings = type(
        "S",
        (),
        {
            "smtp_enabled": True,
            "smtp_from": "scheduler@example.com",
            "alert_email": "operator@example.com",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "u",
            "smtp_pass": "p",
            "base_url": "http://localhost:8000",
        },
    )()
    monkeypatch.setattr(alerts_mod, "settings", fake_settings)

    sent_calls = []

    async def fake_send(message, **kwargs):
        sent_calls.append((message, kwargs))

    import types

    fake_module = types.ModuleType("aiosmtplib")
    fake_module.send = fake_send

    with patch.dict("sys.modules", {"aiosmtplib": fake_module}):
        alert = {"key": "k", "severity": "critical", "title": "T", "detail": "D"}
        first = await notify_via_email([alert])
        second = await notify_via_email([alert])  # within 24h — should de-dup

    assert first == 1
    assert second == 0
    assert len(sent_calls) == 1
