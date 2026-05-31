"""
TikTok Content Posting API integration.

The TikTok API restricts unaudited apps (and most production apps) to uploading
videos as drafts the user finalises in-app — `PULL_FROM_URL` plus the standard
inbox flow. We do not call any "direct post" endpoint; per the spec the user
finishes publishing inside the TikTok app.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

TIKTOK_BASE = "https://open.tiktokapis.com/v2"
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class TikTokApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, body: dict[str, Any] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


def _raise_for_error(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        raise TikTokApiError(f"TikTok API {resp.status_code}: non-JSON response", status_code=resp.status_code)
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    code = err.get("code")
    if resp.status_code >= 400 or (code and code != "ok"):
        raise TikTokApiError(
            err.get("message") or f"TikTok API {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )
    return body


async def upload_to_tiktok_inbox(
    access_token: str,
    video_url: str,
    caption: str | None = None,
) -> str:
    """Send a video into the user's TikTok drafts. They publish from the app.

    Returns the TikTok publish_id used to query status later.

    `caption` is accepted but unused: TikTok's inbox/PULL_FROM_URL flow does not
    accept caption metadata — the user enters it in-app when finalising. Direct
    posting requires audited app + extra scopes; this flow does not.
    """
    _ = caption  # see docstring
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(
            f"{TIKTOK_BASE}/post/publish/inbox/video/init/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": video_url,
                }
            },
        )
        body = _raise_for_error(r)
        return body["data"]["publish_id"]


async def get_publish_status(access_token: str, publish_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(
            f"{TIKTOK_BASE}/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
        return _raise_for_error(r)["data"]


async def exchange_tiktok_code(code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(
            f"{TIKTOK_BASE}/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        body = _raise_for_error(r)
        # TikTok's token endpoint returns top-level fields, not wrapped in `data`.
        return body if "access_token" in body else body.get("data", body)


async def refresh_tiktok_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.post(
            f"{TIKTOK_BASE}/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        body = _raise_for_error(r)
        return body if "access_token" in body else body.get("data", body)


async def ensure_fresh_tiktok_token(db, token, *, force: bool = False, min_remaining_seconds: int = 600):
    """Return a token row guaranteed to have a non-expired access_token.

    TikTok access tokens are short-lived (24h). If the row is within `min_remaining_seconds`
    of expiry — or `force=True` — refresh using refresh_token and persist the new pair.
    """
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    if not force and token.expires_at and (token.expires_at - now).total_seconds() > min_remaining_seconds:
        return token

    if not token.refresh_token:
        raise TikTokApiError("TikTok refresh_token missing. Reconnect TikTok in the UI.")

    data = await refresh_tiktok_token(token.refresh_token)
    token.access_token = data["access_token"]
    if data.get("refresh_token"):
        token.refresh_token = data["refresh_token"]
    expires_in = int(data.get("expires_in") or 0)
    if expires_in:
        token.expires_at = now + timedelta(seconds=expires_in)
    token.updated_at = now
    db.add(token)
    return token


# Backwards-compatible alias used by the scheduler.
upload_to_tiktok_drafts = upload_to_tiktok_inbox


async def get_tiktok_user(access_token: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        r = await client.get(
            f"{TIKTOK_BASE}/user/info/",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "open_id,display_name,username"},
        )
        body = _raise_for_error(r)
        user = body["data"]["user"]
        return {
            "id": user["open_id"],
            "label": user.get("username") or user.get("display_name") or user["open_id"],
        }
