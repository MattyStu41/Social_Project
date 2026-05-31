"""Per-platform circuit breaker.

Tracks consecutive publish failures per platform. When the count reaches the
configured threshold (default 3), the platform is paused for a cool-down
window (default 15 minutes). While paused, the scheduler must not attempt
publishes to that platform; the existing post stays `pending` so it picks
up automatically once the breaker resets.

State is in-memory by design. A process restart resets every breaker — that
is the desired behaviour: a restart usually means a fix was deployed, and
the operator wants the queue to try again immediately. The pause state is
surfaced through `/api/platforms/status` so the UI can warn the operator.

This module is intentionally tiny and synchronous: a single asyncio process
owns the scheduler, so there is no cross-task contention worth a lock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from config import settings


@dataclass
class _PlatformState:
    consecutive_failures: int = 0
    paused_until: datetime | None = None
    last_error: str | None = None
    last_failure_at: datetime | None = None


@dataclass
class CircuitBreaker:
    threshold: int = field(default_factory=lambda: settings.circuit_breaker_threshold)
    cooldown_seconds: int = field(default_factory=lambda: settings.circuit_breaker_cooldown_seconds)
    _state: dict[str, _PlatformState] = field(default_factory=dict)

    def _get(self, platform: str) -> _PlatformState:
        return self._state.setdefault(platform, _PlatformState())

    def is_open(self, platform: str, *, now: datetime | None = None) -> bool:
        """True iff the platform is currently paused (do not publish)."""
        now = now or datetime.now(UTC)
        s = self._get(platform)
        if s.paused_until is None:
            return False
        if s.paused_until <= now:
            s.paused_until = None
            s.consecutive_failures = 0
            return False
        return True

    def record_success(self, platform: str) -> None:
        s = self._get(platform)
        s.consecutive_failures = 0
        s.paused_until = None
        s.last_error = None
        s.last_failure_at = None

    def record_failure(
        self,
        platform: str,
        error: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Record a failure. Returns True iff this failure tripped the breaker."""
        now = now or datetime.now(UTC)
        s = self._get(platform)
        s.consecutive_failures += 1
        s.last_error = error
        s.last_failure_at = now
        tripped = False
        if s.consecutive_failures >= self.threshold and s.paused_until is None:
            s.paused_until = now + timedelta(seconds=self.cooldown_seconds)
            tripped = True
        return tripped

    def status(self, platform: str, *, now: datetime | None = None) -> dict:
        """Snapshot for the UI. Side-effect: clears expired pauses."""
        now = now or datetime.now(UTC)
        # Trigger the auto-clear path before reporting.
        _ = self.is_open(platform, now=now)
        s = self._get(platform)
        return {
            "consecutive_failures": s.consecutive_failures,
            "paused_until": s.paused_until.isoformat() if s.paused_until else None,
            "paused": s.paused_until is not None,
            "last_error": s.last_error,
            "last_failure_at": s.last_failure_at.isoformat() if s.last_failure_at else None,
        }

    def reset(self, platform: str | None = None) -> None:
        """Manual reset (used by the UI's 'resume' button and by tests)."""
        if platform is None:
            self._state.clear()
        else:
            self._state.pop(platform, None)


# Process-wide singleton. APScheduler runs in this same process; routers
# inspect the same instance through the helpers below.
breaker = CircuitBreaker()


def get_breaker() -> CircuitBreaker:
    return breaker
