"""Unit tests for services.circuit_breaker.CircuitBreaker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.circuit_breaker import CircuitBreaker


def test_breaker_starts_closed():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
    assert cb.is_open("threads") is False
    snap = cb.status("threads")
    assert snap["paused"] is False
    assert snap["consecutive_failures"] == 0


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
    assert cb.record_failure("threads", "boom") is False
    assert cb.record_failure("threads", "boom") is False
    tripped = cb.record_failure("threads", "boom")
    assert tripped is True
    assert cb.is_open("threads") is True
    snap = cb.status("threads")
    assert snap["paused"] is True
    assert snap["consecutive_failures"] == 3
    assert snap["last_error"] == "boom"


def test_breaker_success_resets_counter():
    cb = CircuitBreaker(threshold=3, cooldown_seconds=60)
    cb.record_failure("threads", "x")
    cb.record_failure("threads", "x")
    cb.record_success("threads")
    assert cb.status("threads")["consecutive_failures"] == 0
    assert cb.is_open("threads") is False


def test_breaker_does_not_trip_again_while_already_open():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
    cb.record_failure("threads", "x")
    first = cb.record_failure("threads", "x")  # trips
    second = cb.record_failure("threads", "x")  # still open, not a fresh trip
    assert first is True
    assert second is False


def test_breaker_auto_clears_after_cooldown():
    cb = CircuitBreaker(threshold=2, cooldown_seconds=60)
    cb.record_failure("threads", "x")
    cb.record_failure("threads", "x")
    assert cb.is_open("threads") is True

    future = datetime.now(UTC) + timedelta(seconds=61)
    assert cb.is_open("threads", now=future) is False
    # And it stays closed for the next call (state was mutated).
    assert cb.is_open("threads") is False


def test_breaker_reset_clears_one_platform_only():
    cb = CircuitBreaker(threshold=1, cooldown_seconds=60)
    cb.record_failure("threads", "x")
    cb.record_failure("instagram", "x")
    cb.reset("threads")
    assert cb.is_open("threads") is False
    assert cb.is_open("instagram") is True


def test_breaker_reset_all():
    cb = CircuitBreaker(threshold=1, cooldown_seconds=60)
    cb.record_failure("threads", "x")
    cb.record_failure("instagram", "x")
    cb.reset()
    assert cb.is_open("threads") is False
    assert cb.is_open("instagram") is False


def test_breaker_status_shape():
    cb = CircuitBreaker(threshold=1, cooldown_seconds=60)
    cb.record_failure("threads", "kaboom")
    s = cb.status("threads")
    assert set(s.keys()) == {
        "consecutive_failures",
        "paused_until",
        "paused",
        "last_error",
        "last_failure_at",
    }
    assert s["paused"] is True
    assert s["last_error"] == "kaboom"
