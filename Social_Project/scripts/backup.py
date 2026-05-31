#!/usr/bin/env python3
"""Dump scheduled_posts, platform_tokens (with secrets redacted), drafts,
post_analytics, and app_settings to a single JSON file (Deliverable 12).

Usage:
    python scripts/backup.py [--out backup-YYYY-MM-DD.json]

Connects to DATABASE_URL exactly like the app does. The output file is
human-readable JSON with one top-level object per table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

import database
from models import (
    AppSetting,
    Draft,
    PlatformToken,
    PostAnalytics,
    ScheduledPost,
    SubstackSeen,
)

REDACTED = "***REDACTED***"


def _serialise(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "value"):  # Enum
        return v.value
    if hasattr(v, "hex"):  # UUID
        return str(v)
    return v


def _row_to_dict(row, *, redact: set[str] | None = None) -> dict[str, Any]:
    redact = redact or set()
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        val = getattr(row, name)
        out[name] = REDACTED if name in redact else _serialise(val)
    return out


async def build_backup() -> dict[str, Any]:
    async with database.AsyncSessionLocal() as db:
        posts = (await db.execute(select(ScheduledPost))).scalars().all()
        tokens = (await db.execute(select(PlatformToken))).scalars().all()
        drafts = (await db.execute(select(Draft))).scalars().all()
        analytics = (await db.execute(select(PostAnalytics))).scalars().all()
        settings_rows = (await db.execute(select(AppSetting))).scalars().all()
        seen = (await db.execute(select(SubstackSeen))).scalars().all()

    return {
        "schema_version": "1",
        "exported_at": datetime.now(UTC).isoformat(),
        "scheduled_posts": [_row_to_dict(p) for p in posts],
        "platform_tokens": [
            _row_to_dict(t, redact={"access_token", "refresh_token"}) for t in tokens
        ],
        "drafts": [_row_to_dict(d) for d in drafts],
        "post_analytics": [_row_to_dict(a) for a in analytics],
        "app_settings": [_row_to_dict(s) for s in settings_rows],
        "substack_seen": [_row_to_dict(s) for s in seen],
    }


async def main_async(out_path: Path) -> int:
    payload = await build_backup()
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    print(f"Wrote {out_path} ({counts})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(f"backup-{datetime.now(UTC).strftime('%Y-%m-%d-%H%M')}.json"),
        help="Output path (default: backup-YYYY-MM-DD-HHMM.json)",
    )
    args = ap.parse_args()
    return asyncio.run(main_async(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
