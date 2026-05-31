from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import PlatformToken
from security import require_admin
from services.circuit_breaker import get_breaker

router = APIRouter(dependencies=[Depends(require_admin)])

SUPPORTED = ("threads", "instagram", "tiktok")


class BreakerStatus(BaseModel):
    paused: bool
    paused_until: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    last_failure_at: str | None = None


class ConnectedAccount(BaseModel):
    """One row from platform_tokens (D16: a platform can have several)."""

    account_id: str
    user_id: str | None = None
    account_label: str | None = None
    expires_at: datetime | None = None
    expired: bool = False
    expires_in_days: int | None = None


class PlatformWithAccounts(BaseModel):
    """Per-platform view: zero or more connected accounts + the shared breaker.

    The circuit breaker is intentionally per-platform (not per-account):
    when Meta is throwing errors, every Meta account is affected, so trips
    should apply to the whole platform. Per-account breakers would mask the
    real failure mode.
    """

    accounts: list[ConnectedAccount]
    breaker: BreakerStatus


@router.get("/status", response_model=dict[str, PlatformWithAccounts])
async def platform_status(
    db: AsyncSession = Depends(get_db),
) -> dict[str, PlatformWithAccounts]:
    rows = (
        await db.execute(select(PlatformToken).order_by(PlatformToken.created_at))
    ).scalars().all()
    now = datetime.now(UTC)
    breaker = get_breaker()

    out: dict[str, PlatformWithAccounts] = {
        p: PlatformWithAccounts(
            accounts=[],
            breaker=BreakerStatus(**breaker.status(p)),
        )
        for p in SUPPORTED
    }
    for token in rows:
        if token.platform not in out:
            continue
        expires_in_days: int | None = None
        expired = False
        if token.expires_at:
            expires_at = token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            delta = expires_at - now
            expired = delta.total_seconds() < 0
            expires_in_days = max(int(delta.total_seconds() // 86400), 0)
        out[token.platform].accounts.append(
            ConnectedAccount(
                account_id=str(token.id),
                user_id=token.user_id,
                account_label=token.account_label,
                expires_at=token.expires_at,
                expired=expired,
                expires_in_days=expires_in_days,
            )
        )
    return out


@router.post("/{platform}/resume", status_code=200)
async def resume_platform(platform: str) -> dict:
    """Operator-initiated reset of the circuit breaker for one platform.

    Resets ALL accounts on that platform (breaker is platform-wide).
    """
    if platform not in SUPPORTED:
        raise HTTPException(status_code=404, detail="Unknown platform")
    breaker = get_breaker()
    breaker.reset(platform)
    return {"resumed": platform}
