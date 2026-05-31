"""Operator settings (Deliverable 7 + Deliverable 13).

GET  /api/settings              — return the current settings (timezone, retention_days).
PATCH /api/settings             — update one or more settings. Partial updates allowed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings as app_settings
from database import get_db
from models import AppSetting, AuditLog, PostStatus, ScheduledPost
from security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

# Setting keys + their default values. New settings go here; the DB is purely
# an override store on top of these defaults.
DEFAULTS: dict[str, Any] = {
    "timezone": "UTC",
    "retention_days": app_settings.retention_default_days,
}


class SettingsResponse(BaseModel):
    timezone: str
    retention_days: int


class SettingsPatch(BaseModel):
    timezone: str | None = Field(default=None, max_length=80)
    retention_days: int | None = Field(default=None, ge=1, le=10000)


async def _load_all(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(AppSetting))).scalars().all()
    out = dict(DEFAULTS)
    for r in rows:
        if r.key in DEFAULTS:
            out[r.key] = r.value
    return out


async def _set(db: AsyncSession, key: str, value: Any) -> None:
    row = (
        await db.execute(select(AppSetting).where(AppSetting.key == key))
    ).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


@router.get("", response_model=SettingsResponse)
async def get_settings_(db: AsyncSession = Depends(get_db)) -> SettingsResponse:
    return SettingsResponse(**await _load_all(db))


@router.patch("", response_model=SettingsResponse)
async def patch_settings(
    payload: SettingsPatch, db: AsyncSession = Depends(get_db)
) -> SettingsResponse:
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        await _set(db, k, v)
    await db.commit()
    return SettingsResponse(**await _load_all(db))


# ---------- Retention purge (Deliverable 13) ----------


class PurgePreviewResponse(BaseModel):
    threshold: str
    candidate_count: int
    retention_days: int


class PurgeRequest(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=10000)
    confirm: bool = Field(default=False)


class PurgeResponse(BaseModel):
    deleted_count: int
    retention_days: int
    audit_id: str


async def _effective_retention(db: AsyncSession, override: int | None) -> int:
    if override is not None:
        return override
    settings_now = await _load_all(db)
    return int(settings_now["retention_days"])


@router.get("/purge", response_model=PurgePreviewResponse)
async def preview_purge(
    days: int | None = None, db: AsyncSession = Depends(get_db)
) -> PurgePreviewResponse:
    """Show how many posts would be purged under the current retention setting."""
    eff = await _effective_retention(db, days)
    threshold = datetime.now(UTC) - timedelta(days=eff)
    rows = (
        await db.execute(
            select(ScheduledPost.id).where(
                ScheduledPost.status == PostStatus.published,
                ScheduledPost.scheduled_at < threshold,
            )
        )
    ).scalars().all()
    return PurgePreviewResponse(
        threshold=threshold.isoformat(),
        candidate_count=len(rows),
        retention_days=eff,
    )


@router.post("/purge", response_model=PurgeResponse)
async def execute_purge(
    payload: PurgeRequest, db: AsyncSession = Depends(get_db)
) -> PurgeResponse:
    """Delete published posts older than `retention_days`. Idempotent:
    running twice in a row deletes nothing the second time. Logs every
    invocation to audit_log so the operator can answer 'did I really delete
    those posts on date X?'.
    """
    eff = await _effective_retention(db, payload.retention_days)
    threshold = datetime.now(UTC) - timedelta(days=eff)
    rows = (
        await db.execute(
            select(ScheduledPost).where(
                ScheduledPost.status == PostStatus.published,
                ScheduledPost.scheduled_at < threshold,
            )
        )
    ).scalars().all()

    candidate_ids = [str(r.id) for r in rows]

    if not payload.confirm:
        # Dry run — write an audit row, return what would have been deleted.
        log_row = AuditLog(
            action="purge_dry_run",
            payload={
                "retention_days": eff,
                "threshold": threshold.isoformat(),
                "candidate_count": len(candidate_ids),
                "candidate_ids": candidate_ids[:200],
            },
        )
        db.add(log_row)
        await db.commit()
        return PurgeResponse(
            deleted_count=0,
            retention_days=eff,
            audit_id=str(log_row.id),
        )

    for row in rows:
        await db.delete(row)
    log_row = AuditLog(
        action="purge_executed",
        payload={
            "retention_days": eff,
            "threshold": threshold.isoformat(),
            "deleted_count": len(candidate_ids),
            "deleted_ids": candidate_ids[:200],
        },
    )
    db.add(log_row)
    await db.commit()
    return PurgeResponse(
        deleted_count=len(candidate_ids),
        retention_days=eff,
        audit_id=str(log_row.id),
    )


class AuditEntry(BaseModel):
    id: str
    at: datetime
    action: str
    payload: dict[str, Any]


@router.get("/audit", response_model=list[AuditEntry])
async def audit_trail(
    db: AsyncSession = Depends(get_db), limit: int = 100
) -> list[AuditEntry]:
    rows = (
        await db.execute(select(AuditLog).order_by(desc(AuditLog.at)).limit(limit))
    ).scalars().all()
    return [
        AuditEntry(id=str(r.id), at=r.at, action=r.action, payload=r.payload or {})
        for r in rows
    ]
