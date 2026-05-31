"""
Admin auth, OAuth state CSRF tokens, and session cookies.

This is a single-user, self-hosted tool. Auth model: one bcrypt-hashed admin
password gates the UI; on login we issue a signed cookie containing only an
expiry. OAuth flows verify a one-time CSRF token returned in the `state`
parameter via the same signing key.
"""

from __future__ import annotations

import hmac
import secrets
import time
from hashlib import sha256

import bcrypt
from fastapi import Cookie, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import settings

SESSION_COOKIE = "jack_session"
OAUTH_STATE_COOKIE = "jack_oauth_state"
OAUTH_STATE_TTL_SECONDS = 600


def _serializer() -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SESSION_SECRET is not configured. The app cannot issue sessions.",
        )
    return URLSafeTimedSerializer(settings.session_secret, salt="jack-session-v1")


def verify_password(password: str) -> bool:
    if not settings.admin_password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), settings.admin_password_hash.encode("utf-8"))
    except ValueError:
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def issue_session_token() -> str:
    return _serializer().dumps({"iat": int(time.time())})


def session_cookie_max_age() -> int:
    return settings.session_ttl_hours * 3600


def _validate_session_token(token: str) -> bool:
    try:
        _serializer().loads(token, max_age=session_cookie_max_age())
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_admin(
    request: Request,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> None:
    """Dependency: rejects unless a valid admin session cookie is present.

    When auth is disabled (no admin hash configured) this dependency raises a
    503 so that protected endpoints fail closed rather than open.
    """
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin auth is not configured. Set ADMIN_PASSWORD_HASH and SESSION_SECRET.",
        )
    if not session or not _validate_session_token(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


# ---------- OAuth state (CSRF) ----------


def mint_oauth_state(platform: str) -> str:
    nonce = secrets.token_urlsafe(24)
    payload = f"{int(time.time())}:{platform}:{nonce}"
    sig = hmac.new(
        settings.session_secret.encode("utf-8") or b"unconfigured",
        payload.encode("utf-8"),
        sha256,
    ).hexdigest()[:32]
    return f"{payload}:{sig}"


def verify_oauth_state(state: str, platform: str) -> bool:
    if not state:
        return False
    parts = state.split(":")
    if len(parts) != 4:
        return False
    ts_s, plat, _nonce, sig = parts
    if plat != platform:
        return False
    try:
        ts = int(ts_s)
    except ValueError:
        return False
    if time.time() - ts > OAUTH_STATE_TTL_SECONDS:
        return False
    expected_payload = f"{ts_s}:{plat}:{_nonce}"
    expected_sig = hmac.new(
        settings.session_secret.encode("utf-8") or b"unconfigured",
        expected_payload.encode("utf-8"),
        sha256,
    ).hexdigest()[:32]
    return hmac.compare_digest(sig, expected_sig)
