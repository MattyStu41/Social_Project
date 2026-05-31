"""Drafts endpoints (Deliverable 5).

* GET /api/drafts                — list drafts not yet promoted
* GET /api/drafts/{id}           — one draft + its pull-quote candidates
* DELETE /api/drafts/{id}        — discard a draft
* POST /api/drafts/{id}/promote  — convert a draft into a ScheduledPost
                                    (operator picks a quote + platforms + time)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Draft, PostStatus, ScheduledPost
from schemas import (
    PLATFORM_CAPTION_LIMITS,
    PLATFORM_HASHTAG_LIMITS,
    SUPPORTED_PLATFORMS,
    _hashtag_count,
)
from security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class DraftResponse(BaseModel):
    id: uuid.UUID
    source_kind: str
    source_url: str | None
    title: str | None
    pull_quotes: list[str]
    suggested_caption: str | None
    media_url: str | None
    created_at: datetime
    promoted_post_id: uuid.UUID | None


class PromoteDraftRequest(BaseModel):
    caption: str = Field(..., min_length=1, max_length=2200)
    platforms: list[str] = Field(..., min_length=1)
    media_url: str | None = Field(default=None, max_length=2048)
    media_type: str | None = None
    scheduled_at: datetime

    @field_validator("platforms")
    @classmethod
    def _check(cls, v: list[str]) -> list[str]:
        lowered = [p.lower() for p in v]
        unknown = [p for p in lowered if p not in SUPPORTED_PLATFORMS]
        if unknown:
            raise ValueError(f"Unsupported platform(s): {', '.join(unknown)}")
        return lowered

    @field_validator("scheduled_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone.")
        return v.astimezone(UTC)


def _to_response(d: Draft) -> DraftResponse:
    return DraftResponse(
        id=d.id,
        source_kind=d.source_kind,
        source_url=d.source_url,
        title=d.title,
        pull_quotes=d.pull_quotes or [],
        suggested_caption=d.suggested_caption,
        media_url=d.media_url,
        created_at=d.created_at,
        promoted_post_id=d.promoted_post_id,
    )


@router.get("", response_model=list[DraftResponse])
async def list_drafts(
    db: AsyncSession = Depends(get_db),
    include_promoted: bool = False,
) -> list[DraftResponse]:
    stmt = select(Draft).order_by(desc(Draft.created_at))
    if not include_promoted:
        stmt = stmt.where(Draft.promoted_post_id.is_(None))
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_response(d) for d in rows]


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(draft_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DraftResponse:
    d = (await db.execute(select(Draft).where(Draft.id == draft_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    return _to_response(d)


@router.delete("/{draft_id}", status_code=204, response_class=Response)
async def delete_draft(draft_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    d = (await db.execute(select(Draft).where(Draft.id == draft_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    await db.delete(d)
    await db.commit()
    return Response(status_code=204)


def _validate_caption(platform: str, caption: str) -> None:
    if len(caption) > PLATFORM_CAPTION_LIMITS[platform]:
        raise HTTPException(
            status_code=422,
            detail=f"{platform} caption is {len(caption)} chars; limit is {PLATFORM_CAPTION_LIMITS[platform]}.",
        )
    if _hashtag_count(caption) > PLATFORM_HASHTAG_LIMITS[platform]:
        raise HTTPException(
            status_code=422,
            detail=f"{platform} has too many hashtags (limit {PLATFORM_HASHTAG_LIMITS[platform]}).",
        )


@router.post("/{draft_id}/promote")
async def promote_draft(
    draft_id: uuid.UUID,
    payload: PromoteDraftRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = (await db.execute(select(Draft).where(Draft.id == draft_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="Draft not found")
    if d.promoted_post_id is not None:
        raise HTTPException(status_code=400, detail="Draft already promoted.")

    # Per-platform caption limits (mirrors schemas._validate_caption_for).
    for platform in payload.platforms:
        _validate_caption(platform, payload.caption)

    post = ScheduledPost(
        caption=payload.caption,
        platforms=payload.platforms,
        media_url=payload.media_url or d.media_url,
        media_type=payload.media_type,
        scheduled_at=payload.scheduled_at,
        status=PostStatus.pending,
        platform_results={},
        platform_overrides={},
        attempts=[],
    )
    db.add(post)
    await db.flush()  # need post.id for back-reference
    d.promoted_post_id = post.id
    await db.commit()
    return {"draft_id": str(d.id), "post_id": str(post.id)}
