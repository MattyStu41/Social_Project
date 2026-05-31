"""
Pydantic request/response schemas. Caption length limits enforced per platform.

Per-platform overrides (D4) live in `platform_overrides`. Keys are platform
names; values are `PlatformOverride` blocks. Each override is validated
against that platform's limits (caption length, hashtag count). Empty
overrides are dropped before persistence so the scheduler can rely on
`overrides[p] is None` meaning "use the top-level fields".

What we validate at draft time (rejected with clear reasons, never silently
truncated):
  * caption length per platform (Threads 500, IG/TikTok 2200)
  * hashtag count per platform (Threads/IG 30, TikTok 100)
  * media presence for IG (requires image or video) and TikTok (requires
    video)
  * TikTok media_type must be VIDEO

What we deliberately do NOT validate (would require fetching the media URL
and inspecting it — would add a per-draft network call, which is out of
scope for a personal-use scheduler with no third-party services):
  * video duration vs platform max (Threads ≤5min, IG Reels ≤15min,
    TikTok ≤10min)
  * aspect ratio (IG Reels expects 9:16; off-spec media renders but is
    cropped/letterboxed)
  * file size

The operator is expected to use platform-compliant media. Publish failures
from out-of-spec media surface via the platform's error response in
`platform_results[p].error` and trip the circuit breaker like any other
failure.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models import PostStatus

SUPPORTED_PLATFORMS = {"threads", "instagram", "tiktok"}
SUPPORTED_MEDIA_TYPES = {"IMAGE", "VIDEO"}

PLATFORM_CAPTION_LIMITS = {
    "threads": 500,
    "instagram": 2200,
    "tiktok": 2200,
}

# Hashtag count limits per platform (D4 validation). Instagram is the strict
# one — anything past 30 actively suppresses reach.
PLATFORM_HASHTAG_LIMITS = {
    "threads": 30,
    "instagram": 30,
    "tiktok": 100,
}

_HASHTAG_RE = re.compile(r"(?<![\w&])#([A-Za-z][\w]{0,99})")


def _hashtag_count(caption: str) -> int:
    return len(_HASHTAG_RE.findall(caption or ""))


def _normalise_platforms(v: list[str]) -> list[str]:
    lowered = [p.lower() for p in v]
    unknown = [p for p in lowered if p not in SUPPORTED_PLATFORMS]
    if unknown:
        raise ValueError(f"Unsupported platform(s): {', '.join(unknown)}")
    seen: set[str] = set()
    out: list[str] = []
    for p in lowered:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _normalise_media_type(v: str | None) -> str | None:
    if v is None:
        return v
    upper = v.upper()
    if upper not in SUPPORTED_MEDIA_TYPES:
        raise ValueError(f"media_type must be one of: {', '.join(sorted(SUPPORTED_MEDIA_TYPES))}")
    return upper


def _coerce_utc_required(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("scheduled_at must include a timezone (e.g. 2026-01-01T12:00:00Z)")
    return v.astimezone(UTC)


def _validate_caption_for(platform: str, caption: str) -> None:
    """Reject at draft time per D4 spec — no silent truncation."""
    limit = PLATFORM_CAPTION_LIMITS[platform]
    if len(caption) > limit:
        raise ValueError(
            f"{platform} caption is {len(caption)} chars; limit is {limit}."
        )
    h = _hashtag_count(caption)
    h_limit = PLATFORM_HASHTAG_LIMITS[platform]
    if h > h_limit:
        raise ValueError(
            f"{platform} caption has {h} hashtags; limit is {h_limit}."
        )


class PlatformOverride(BaseModel):
    """Per-platform caption / media override block (D4)."""

    caption: str | None = Field(default=None, min_length=1, max_length=2200)
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = None

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, v: str | None) -> str | None:
        return _normalise_media_type(v)


class ScheduledPostCreate(BaseModel):
    caption: str = Field(..., min_length=1, max_length=2200)
    platforms: list[str] = Field(..., min_length=1)
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = None
    scheduled_at: datetime
    # D4: optional per-platform overrides. Unknown platforms here raise.
    platform_overrides: dict[str, PlatformOverride] = Field(default_factory=dict)
    # D16: optional per-platform account selection. Maps platform name → token
    # UUID. Empty / unset = "use the default (first) account for each target
    # platform". Validation happens at the router layer where we have a DB
    # handle to confirm the referenced account exists and matches the platform.
    platform_accounts: dict[str, uuid.UUID] = Field(default_factory=dict)

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, v: list[str]) -> list[str]:
        return _normalise_platforms(v)

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, v: str | None) -> str | None:
        return _normalise_media_type(v)

    @field_validator("scheduled_at")
    @classmethod
    def _coerce_utc(cls, v: datetime) -> datetime:
        return _coerce_utc_required(v)

    @field_validator("platform_overrides")
    @classmethod
    def _override_keys_known(
        cls, v: dict[str, PlatformOverride]
    ) -> dict[str, PlatformOverride]:
        unknown = [k for k in v if k.lower() not in SUPPORTED_PLATFORMS]
        if unknown:
            raise ValueError(f"Unsupported override platform(s): {', '.join(unknown)}")
        # Drop fully-empty overrides (no caption, no media): the scheduler
        # treats absence as "fall back to top-level fields".
        return {
            k.lower(): ov
            for k, ov in v.items()
            if ov.caption is not None or ov.media_url is not None or ov.media_type is not None
        }

    @model_validator(mode="after")
    def _cross_field_checks(self) -> ScheduledPostCreate:
        if "instagram" in self.platforms and not self._effective_media_url("instagram"):
            raise ValueError("Instagram requires media_url (image or video).")
        if "tiktok" in self.platforms:
            if not self._effective_media_url("tiktok"):
                raise ValueError("TikTok requires media_url pointing to a video.")
            mt = self._effective_media_type("tiktok")
            if mt and mt != "VIDEO":
                raise ValueError("TikTok only accepts media_type=VIDEO.")

        if self.media_url and not self.media_type:
            url = self.media_url.lower().split("?", 1)[0]
            if url.endswith((".mp4", ".mov", ".m4v", ".webm")):
                object.__setattr__(self, "media_type", "VIDEO")
            elif url.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic")):
                object.__setattr__(self, "media_type", "IMAGE")
            else:
                raise ValueError("media_type is required when media_url extension is ambiguous.")

        # Per-platform caption / hashtag limits (D4): every targeted platform's
        # effective caption must fit. Reject at draft time.
        for platform in self.platforms:
            effective = self._effective_caption(platform)
            _validate_caption_for(platform, effective)

        # Reject overrides for platforms not in self.platforms.
        for k in self.platform_overrides:
            if k not in self.platforms:
                raise ValueError(
                    f"Override for {k!r} but {k} is not in selected platforms."
                )
        return self

    def _effective_caption(self, platform: str) -> str:
        ov = self.platform_overrides.get(platform)
        if ov and ov.caption is not None:
            return ov.caption
        return self.caption

    def _effective_media_url(self, platform: str) -> str | None:
        ov = self.platform_overrides.get(platform)
        if ov and ov.media_url is not None:
            return ov.media_url
        return self.media_url

    def _effective_media_type(self, platform: str) -> str | None:
        ov = self.platform_overrides.get(platform)
        if ov and ov.media_type is not None:
            return ov.media_type
        return self.media_type


class ScheduledPostUpdate(BaseModel):
    caption: str | None = Field(default=None, min_length=1, max_length=2200)
    platforms: list[str] | None = None
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = None
    scheduled_at: datetime | None = None
    platform_overrides: dict[str, PlatformOverride] | None = None
    platform_accounts: dict[str, uuid.UUID] | None = None

    @field_validator("platforms")
    @classmethod
    def _validate_platforms(cls, v: list[str] | None) -> list[str] | None:
        return _normalise_platforms(v) if v is not None else v

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, v: str | None) -> str | None:
        return _normalise_media_type(v)

    @field_validator("scheduled_at")
    @classmethod
    def _coerce_utc(cls, v: datetime | None) -> datetime | None:
        return _coerce_utc_required(v) if v is not None else v


class ScheduledPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    caption: str
    platforms: list[str]
    media_url: str | None
    media_type: str | None
    scheduled_at: datetime
    status: PostStatus
    platform_results: dict[str, Any]
    platform_overrides: dict[str, Any] = Field(default_factory=dict)
    platform_accounts: dict[str, Any] = Field(default_factory=dict)
    attempts: list[Any]
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class PlatformStatus(BaseModel):
    connected: bool
    user_id: str | None = None
    account_label: str | None = None
    expires_at: datetime | None = None
    expired: bool = False


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1)
