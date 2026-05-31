from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import PlatformToken
from security import require_admin
from services.alerts import compute_alerts

router = APIRouter()


@router.get("/config")
async def public_config() -> dict:
    """Frontend-visible config: which platforms are wired up at the env level
    (i.e. credentials present) and whether auth is enabled. No secrets leak."""
    return {
        "auth_required": settings.auth_enabled,
        "platforms_available": {
            "threads": bool(settings.meta_app_id and settings.meta_app_secret),
            "instagram": bool(settings.meta_app_id and settings.meta_app_secret),
            "tiktok": bool(settings.tiktok_client_key and settings.tiktok_client_secret),
        },
        "base_url": settings.base_url,
        "version": settings.app_version,
    }


@router.get("/alerts", dependencies=[Depends(require_admin)])
async def alerts(db: AsyncSession = Depends(get_db)) -> dict:
    """Current operator-facing alerts (token expiry, paused platforms, missing
    credentials). Computed on demand from current state, so always fresh.

    The dashboard polls this endpoint and renders a top-of-page banner.
    """
    tokens = (await db.execute(select(PlatformToken))).scalars().all()
    items = compute_alerts(list(tokens))
    return {"alerts": items, "count": len(items)}
