"""
Meta platform integration: Threads + Instagram Graph API.

Threads uses the dedicated graph.threads.net host. Instagram uses the standard
Facebook Graph API and requires a Business/Creator IG account linked to a
Facebook Page the OAuth user manages.

All HTTP calls go through `_post`/`_get` which raise a `MetaApiError` carrying
the parsed Meta error body so the scheduler can record it cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = f"https://graph.facebook.com/{settings.meta_graph_version}"
THREADS_BASE = "https://graph.threads.net/v1.0"

# Threads media containers take longer to process for video; IG Reels even more.
_THREADS_POLL_ATTEMPTS = 20
_THREADS_POLL_INTERVAL = 3.0
_IG_POLL_ATTEMPTS = 30
_IG_POLL_INTERVAL = 5.0

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


class MetaApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}


def _extract_error(resp: httpx.Response) -> MetaApiError:
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}
    err = body.get("error") if isinstance(body, dict) else None
    msg = (err or {}).get("message") if isinstance(err, dict) else None
    return MetaApiError(
        msg or f"Meta API {resp.status_code}",
        status_code=resp.status_code,
        body=body if isinstance(body, dict) else {"raw": str(body)},
    )


async def _post(client: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    r = await client.post(url, **kwargs)
    if r.status_code >= 400:
        raise _extract_error(r)
    return r.json()


async def _get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> dict[str, Any]:
    r = await client.get(url, **kwargs)
    if r.status_code >= 400:
        raise _extract_error(r)
    return r.json()


# ---------- Publishing ----------


async def publish_to_threads(
    access_token: str,
    user_id: str,
    caption: str,
    media_url: str | None = None,
    media_type: str | None = None,
) -> str:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        container: dict[str, Any] = {"access_token": access_token, "text": caption}
        if media_url and media_type == "IMAGE":
            container["image_url"] = media_url
            container["media_type"] = "IMAGE"
        elif media_url and media_type == "VIDEO":
            container["video_url"] = media_url
            container["media_type"] = "VIDEO"
        else:
            container["media_type"] = "TEXT"

        created = await _post(client, f"{THREADS_BASE}/{user_id}/threads", data=container)
        container_id = created["id"]

        if media_url:
            for _ in range(_THREADS_POLL_ATTEMPTS):
                await asyncio.sleep(_THREADS_POLL_INTERVAL)
                info = await _get(
                    client,
                    f"{THREADS_BASE}/{container_id}",
                    params={"fields": "status,error_message", "access_token": access_token},
                )
                status_value = info.get("status")
                if status_value == "FINISHED":
                    break
                if status_value == "ERROR":
                    raise MetaApiError(f"Threads media error: {info.get('error_message')}", body=info)
            else:
                raise MetaApiError("Threads media container did not reach FINISHED in time.")

        published = await _post(
            client,
            f"{THREADS_BASE}/{user_id}/threads_publish",
            data={"creation_id": container_id, "access_token": access_token},
        )
        return published["id"]


async def publish_to_instagram(
    access_token: str,
    ig_user_id: str,
    caption: str,
    media_url: str | None = None,
    media_type: str | None = None,
) -> str:
    if not media_url:
        raise MetaApiError("Instagram requires media_url (image or video).")

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        container: dict[str, Any] = {"caption": caption, "access_token": access_token}
        if media_type == "VIDEO":
            container["media_type"] = "REELS"
            container["video_url"] = media_url
        else:
            container["image_url"] = media_url

        created = await _post(client, f"{GRAPH_BASE}/{ig_user_id}/media", data=container)
        container_id = created["id"]

        for _ in range(_IG_POLL_ATTEMPTS):
            await asyncio.sleep(_IG_POLL_INTERVAL)
            info = await _get(
                client,
                f"{GRAPH_BASE}/{container_id}",
                params={"fields": "status_code,status", "access_token": access_token},
            )
            code = info.get("status_code") or info.get("status")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise MetaApiError("Instagram media processing failed.", body=info)
        else:
            raise MetaApiError("Instagram media container did not reach FINISHED in time.")

        published = await _post(
            client,
            f"{GRAPH_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
        )
        return published["id"]


# ---------- OAuth ----------


async def exchange_threads_code_for_token(code: str, redirect_uri: str) -> dict:
    """Threads OAuth: short-lived token via graph.threads.net, then extend to long-lived (~60 days)."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        short = await _post(
            client,
            "https://graph.threads.net/oauth/access_token",
            data={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        long = await _get(
            client,
            "https://graph.threads.net/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": settings.meta_app_secret,
                "access_token": short["access_token"],
            },
        )
        return long


async def refresh_threads_token(access_token: str) -> dict:
    """Refresh a long-lived Threads token (valid >= 24h, < 60d)."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        return await _get(
            client,
            "https://graph.threads.net/refresh_access_token",
            params={"grant_type": "th_refresh_token", "access_token": access_token},
        )


async def exchange_instagram_code_for_token(code: str, redirect_uri: str) -> dict:
    """Facebook Login → short-lived token → long-lived (~60 days)."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        short = await _get(
            client,
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        long = await _get(
            client,
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short["access_token"],
            },
        )
        return long


async def refresh_long_lived_token(db, token) -> None:
    """Re-extend a long-lived Meta token in place. Uses the platform-appropriate refresh endpoint.

    Threads tokens use `th_refresh_token` on graph.threads.net. Facebook/IG long-lived tokens
    are extended by re-exchanging them through the Graph API's `fb_exchange_token` grant.
    """
    from datetime import datetime, timedelta

    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        if token.platform == "threads":
            data = await _get(
                client,
                "https://graph.threads.net/refresh_access_token",
                params={"grant_type": "th_refresh_token", "access_token": token.access_token},
            )
        elif token.platform == "instagram":
            data = await _get(
                client,
                f"{GRAPH_BASE}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.meta_app_id,
                    "client_secret": settings.meta_app_secret,
                    "fb_exchange_token": token.access_token,
                },
            )
        else:
            return

    token.access_token = data["access_token"]
    expires_in = int(data.get("expires_in") or 0)
    if expires_in:
        token.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    token.updated_at = datetime.now(UTC)
    db.add(token)


async def get_threads_user(access_token: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        data = await _get(
            client,
            f"{THREADS_BASE}/me",
            params={"fields": "id,username", "access_token": access_token},
        )
        return {"id": data["id"], "label": data.get("username") or data["id"]}


async def get_instagram_user(access_token: str) -> dict[str, str]:
    """Find the Instagram Business account linked to the first Page this token manages."""
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
        pages = await _get(
            client,
            f"{GRAPH_BASE}/me/accounts",
            params={"fields": "id,name,instagram_business_account", "access_token": access_token},
        )
        for page in pages.get("data", []):
            iba = page.get("instagram_business_account")
            if iba and iba.get("id"):
                ig_id = iba["id"]
                profile = await _get(
                    client,
                    f"{GRAPH_BASE}/{ig_id}",
                    params={"fields": "username", "access_token": access_token},
                )
                return {"id": ig_id, "label": profile.get("username") or ig_id}
        raise MetaApiError(
            "No Instagram Business account is linked to any Facebook Page on this token. "
            "Link your IG Business/Creator account to a Page in Meta Business Suite and reconnect."
        )
