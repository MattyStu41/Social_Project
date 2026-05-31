"""Repurpose endpoints (Deliverable 6).

* POST /api/repurpose/split   — body + platforms → per-platform candidate list
* POST /api/repurpose/rewrite — text → Ollama-suggested rewrite (operator
                                  approves before use; falls back to input
                                  text when Ollama is not configured).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from config import settings
from schemas import SUPPORTED_PLATFORMS
from security import require_admin
from services.repurpose import ollama_rewrite, repurpose_text

router = APIRouter(dependencies=[Depends(require_admin)])


class SplitRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=200_000)
    platforms: list[str] = Field(..., min_length=1)
    candidates_per_platform: int = Field(default=4, ge=1, le=10)

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v: list[str]) -> list[str]:
        v = [p.lower() for p in v]
        unknown = [p for p in v if p not in SUPPORTED_PLATFORMS]
        if unknown:
            raise ValueError(f"Unsupported platform(s): {', '.join(unknown)}")
        return v


class RewriteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10_000)
    instruction: str | None = Field(default=None, max_length=500)


@router.post("/split")
async def split(payload: SplitRequest) -> dict[str, Any]:
    """Deterministic split. Same input → same output."""
    out: dict[str, list[dict]] = {}
    for platform in payload.platforms:
        out[platform] = repurpose_text(
            payload.body,
            platform=platform,
            candidates_per_platform=payload.candidates_per_platform,
        )
    return {"platforms": out}


@router.post("/rewrite")
async def rewrite(payload: RewriteRequest) -> dict[str, Any]:
    """Optionally call Ollama. If OLLAMA_BASE_URL is not configured, returns
    the original text unchanged plus a flag telling the UI to surface that."""
    rewritten = await ollama_rewrite(payload.text, instruction=payload.instruction)
    return {
        "ollama_enabled": settings.ollama_enabled,
        "rewritten": rewritten,
        "unchanged": rewritten.strip() == payload.text.strip(),
    }
