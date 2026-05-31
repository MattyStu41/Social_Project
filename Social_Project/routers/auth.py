from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models import PlatformToken
from schemas import LoginRequest
from security import (
    SESSION_COOKIE,
    issue_session_token,
    mint_oauth_state,
    require_admin,
    session_cookie_max_age,
    verify_oauth_state,
    verify_password,
)
from services.meta import (
    exchange_instagram_code_for_token,
    exchange_threads_code_for_token,
    get_instagram_user,
    get_threads_user,
)
from services.tiktok import exchange_tiktok_code, get_tiktok_user

log = logging.getLogger("jack.auth")
router = APIRouter()


# ---------- Session ----------


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    if not settings.auth_enabled:
        raise HTTPException(status_code=503, detail="Admin auth is not configured.")
    if not verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid password")
    response.set_cookie(
        SESSION_COOKIE,
        issue_session_token(),
        max_age=session_cookie_max_age(),
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
        path="/",
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
async def session_status(request: Request) -> dict:
    from security import _validate_session_token

    cookie = request.cookies.get(SESSION_COOKIE)
    authed = bool(cookie and _validate_session_token(cookie))
    return {"authenticated": authed, "auth_required": settings.auth_enabled}


# ---------- OAuth: Threads ----------


def _threads_redirect_uri() -> str:
    return f"{settings.base_url}/api/auth/threads/callback"


@router.get("/threads/login", dependencies=[Depends(require_admin)])
async def threads_login() -> RedirectResponse:
    if not settings.meta_app_id:
        raise HTTPException(status_code=503, detail="META_APP_ID is not configured.")
    state = mint_oauth_state("threads")
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": _threads_redirect_uri(),
        "scope": "threads_basic,threads_content_publish",
        "response_type": "code",
        "state": state,
    }
    return RedirectResponse(f"https://threads.net/oauth/authorize?{urlencode(params)}")


@router.get("/threads/callback")
async def threads_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error:
        return RedirectResponse(f"{settings.base_url}/?error={error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not verify_oauth_state(state or "", "threads"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    token_data = await exchange_threads_code_for_token(code, _threads_redirect_uri())
    user = await get_threads_user(token_data["access_token"])
    await _upsert_token(db, "threads", token_data, user)
    return RedirectResponse(f"{settings.base_url}/?connected=threads")


# ---------- OAuth: Instagram ----------


def _instagram_redirect_uri() -> str:
    return f"{settings.base_url}/api/auth/instagram/callback"


@router.get("/instagram/login", dependencies=[Depends(require_admin)])
async def instagram_login() -> RedirectResponse:
    if not settings.meta_app_id:
        raise HTTPException(status_code=503, detail="META_APP_ID is not configured.")
    state = mint_oauth_state("instagram")
    params = {
        "client_id": settings.meta_app_id,
        "redirect_uri": _instagram_redirect_uri(),
        "scope": (
            "instagram_basic,instagram_content_publish,"
            "pages_show_list,pages_read_engagement,business_management"
        ),
        "response_type": "code",
        "state": state,
    }
    return RedirectResponse(
        f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{urlencode(params)}"
    )


@router.get("/instagram/callback")
async def instagram_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error:
        return RedirectResponse(f"{settings.base_url}/?error={error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not verify_oauth_state(state or "", "instagram"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    token_data = await exchange_instagram_code_for_token(code, _instagram_redirect_uri())
    user = await get_instagram_user(token_data["access_token"])
    await _upsert_token(db, "instagram", token_data, user)
    return RedirectResponse(f"{settings.base_url}/?connected=instagram")


# ---------- OAuth: TikTok ----------


def _tiktok_redirect_uri() -> str:
    return f"{settings.base_url}/api/auth/tiktok/callback"


@router.get("/tiktok/login", dependencies=[Depends(require_admin)])
async def tiktok_login() -> RedirectResponse:
    if not settings.tiktok_client_key:
        raise HTTPException(status_code=503, detail="TIKTOK_CLIENT_KEY is not configured.")
    state = mint_oauth_state("tiktok")
    params = {
        "client_key": settings.tiktok_client_key,
        "redirect_uri": _tiktok_redirect_uri(),
        "scope": "user.info.basic,video.upload",
        "response_type": "code",
        "state": state,
    }
    return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}")


@router.get("/tiktok/callback")
async def tiktok_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error:
        return RedirectResponse(f"{settings.base_url}/?error={error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    if not verify_oauth_state(state or "", "tiktok"):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    token_data = await exchange_tiktok_code(code, _tiktok_redirect_uri())
    user = await get_tiktok_user(token_data["access_token"])
    await _upsert_token(db, "tiktok", token_data, user)
    return RedirectResponse(f"{settings.base_url}/?connected=tiktok")


# ---------- Disconnect ----------


@router.delete("/{platform}/disconnect", dependencies=[Depends(require_admin)])
async def disconnect_platform(
    platform: str,
    account_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Disconnect one account.

    If `account_id` is provided, delete that specific row. If omitted and
    there is exactly one account for that platform, delete it. If omitted
    and there are multiple, raise 400 to force the caller to disambiguate.
    """
    rows = (
        await db.execute(select(PlatformToken).where(PlatformToken.platform == platform))
    ).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail="Platform not connected")
    if account_id:
        target = next((r for r in rows if str(r.id) == account_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Account not found for that platform")
        await db.delete(target)
        await db.commit()
        return {"disconnected": platform, "account_id": account_id}
    if len(rows) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{platform} has {len(rows)} connected accounts; "
                "specify ?account_id=<id> to disambiguate."
            ),
        )
    await db.delete(rows[0])
    await db.commit()
    return {"disconnected": platform, "account_id": str(rows[0].id)}


# ---------- helpers ----------


async def _upsert_token(db: AsyncSession, platform: str, token_data: dict, user: dict) -> None:
    """Multi-account-aware upsert (D16).

    Dedupes by (platform, user_id): if a matching row exists, refresh it
    (operator reconnected the same account). Otherwise insert a new row so
    the operator can hold multiple accounts per platform.
    """
    access = token_data["access_token"]
    refresh = token_data.get("refresh_token")
    expires_in = int(token_data.get("expires_in") or 0)
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None
    )

    existing = (
        await db.execute(
            select(PlatformToken).where(
                PlatformToken.platform == platform,
                PlatformToken.user_id == user["id"],
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.access_token = access
        if refresh:
            existing.refresh_token = refresh
        existing.account_label = user.get("label") or user["id"]
        existing.expires_at = expires_at
        existing.updated_at = datetime.now(UTC)
        log.info("Refreshed %s account %s", platform, existing.account_label)
    else:
        db.add(
            PlatformToken(
                platform=platform,
                access_token=access,
                refresh_token=refresh,
                user_id=user["id"],
                account_label=user.get("label") or user["id"],
                expires_at=expires_at,
            )
        )
        log.info("Connected new %s account %s", platform, user.get("label") or user["id"])
    await db.commit()
