from __future__ import annotations

import uuid
import uuid as _uuid_module
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import PlatformToken, PostStatus, ScheduledPost
from scheduler import publish_now as scheduler_publish_now
from schemas import ScheduledPostCreate, ScheduledPostResponse, ScheduledPostUpdate
from security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


async def _validate_and_normalise_accounts(
    db: AsyncSession,
    target_platforms: list[str],
    requested: dict | None,
) -> dict[str, str]:
    """Resolve and validate the optional per-platform account-id mapping (D16).

    * Empty / missing → returns {} (scheduler will use the default account per platform).
    * Each platform in the mapping must be in `target_platforms`.
    * Each referenced account id must exist AND belong to that platform.
    * Output values are stringified UUIDs (JSON-safe).
    """
    if not requested:
        return {}
    accounts = {str(k): str(v) for k, v in requested.items()}
    stray = [p for p in accounts if p not in target_platforms]
    if stray:
        raise HTTPException(
            status_code=400,
            detail=f"platform_accounts pins account(s) for non-targeted platform(s): {stray}",
        )
    try:
        uuid_values = [_uuid_module.UUID(v) for v in accounts.values()]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"platform_accounts contains invalid UUID: {exc}"
        ) from exc
    rows = (
        await db.execute(select(PlatformToken).where(PlatformToken.id.in_(uuid_values)))
    ).scalars().all()
    by_id = {str(r.id): r for r in rows}
    for platform, account_id in accounts.items():
        row = by_id.get(account_id)
        if row is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown account_id {account_id!r} for {platform}.",
            )
        if row.platform != platform:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"account_id {account_id} is a {row.platform} account, "
                    f"but was pinned for {platform}."
                ),
            )
    return accounts


@router.post("", response_model=ScheduledPostResponse, status_code=201)
async def create_post(
    payload: ScheduledPostCreate, db: AsyncSession = Depends(get_db)
) -> ScheduledPost:
    overrides_json = {
        k: v.model_dump(exclude_none=True) for k, v in payload.platform_overrides.items()
    }
    accounts_json = await _validate_and_normalise_accounts(
        db, payload.platforms, payload.platform_accounts
    )
    post = ScheduledPost(
        caption=payload.caption,
        platforms=payload.platforms,
        media_url=payload.media_url,
        media_type=payload.media_type,
        scheduled_at=payload.scheduled_at,
        status=PostStatus.pending,
        platform_results={},
        platform_overrides=overrides_json,
        platform_accounts=accounts_json,
        attempts=[],
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post


@router.get("", response_model=list[ScheduledPostResponse])
async def list_posts(
    status: PostStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduledPost]:
    stmt = select(ScheduledPost).order_by(desc(ScheduledPost.scheduled_at))
    if status is not None:
        stmt = stmt.where(ScheduledPost.status == status)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{post_id}", response_model=ScheduledPostResponse)
async def get_post(post_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ScheduledPost:
    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/{post_id}", response_model=ScheduledPostResponse)
async def update_post(
    post_id: uuid.UUID,
    payload: ScheduledPostUpdate,
    db: AsyncSession = Depends(get_db),
) -> ScheduledPost:
    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status not in (PostStatus.pending, PostStatus.failed):
        raise HTTPException(status_code=400, detail="Only pending or failed posts can be edited")

    data = payload.model_dump(exclude_unset=True)
    if data.get("platform_overrides") is not None:
        data["platform_overrides"] = {
            k: {kk: vv for kk, vv in v.items() if vv is not None}
            for k, v in data["platform_overrides"].items()
        }
    if data.get("platform_accounts") is not None:
        # If the caller is also changing `platforms`, validate against the new
        # set; otherwise validate against the post's existing target platforms.
        target_platforms = data.get("platforms") or list(post.platforms or [])
        data["platform_accounts"] = await _validate_and_normalise_accounts(
            db, target_platforms, data["platform_accounts"]
        )
    for field, value in data.items():
        setattr(post, field, value)
    if post.status == PostStatus.failed:
        # Editing a failed post re-queues it.
        post.status = PostStatus.pending
        post.last_error = None
    post.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(post)
    return post


@router.post("/{post_id}/retry", response_model=ScheduledPostResponse)
async def retry_post(post_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ScheduledPost:
    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != PostStatus.failed:
        raise HTTPException(status_code=400, detail="Only failed posts can be retried")
    post.status = PostStatus.pending
    post.last_error = None
    post.scheduled_at = datetime.now(UTC)
    post.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(post)
    return post


@router.delete("/{post_id}", status_code=204, response_class=Response)
async def delete_post(post_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    await db.delete(post)
    await db.commit()
    return Response(status_code=204)


@router.post("/{post_id}/publish-now", response_model=ScheduledPostResponse)
async def publish_now(post_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ScheduledPost:
    post = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.status != PostStatus.pending:
        raise HTTPException(status_code=400, detail="Post is not in pending state")

    post.scheduled_at = datetime.now(UTC)
    await db.commit()

    await scheduler_publish_now(post_id)

    refreshed = (
        await db.execute(select(ScheduledPost).where(ScheduledPost.id == post_id))
    ).scalar_one()
    return refreshed
