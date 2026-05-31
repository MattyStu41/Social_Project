#!/usr/bin/env python3
"""Re-import a backup file produced by scripts/backup.py onto a fresh database
(Deliverable 12).

Usage:
    python scripts/restore.py path/to/backup.json [--allow-non-empty]

Refuses to write into a non-empty database unless --allow-non-empty is passed.
Even with the flag, the script skips rows whose primary key already exists
(idempotent restore).

Platform tokens come back with `***REDACTED***` in the access_token /
refresh_token fields — you must reconnect each platform via OAuth after
restoring. The script reports this clearly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Uuid as SAUuid
from sqlalchemy import select

import database
from database import init_schema
from models import (
    AppSetting,
    Draft,
    PlatformToken,
    PostAnalytics,
    ScheduledPost,
    SubstackSeen,
)

REDACTED = "***REDACTED***"


def _coerce(table_cls, row: dict[str, Any]) -> dict[str, Any]:
    """Coerce JSON-deserialised values back into the types the model expects.
    Handles datetimes, UUIDs, and (string-backed) enums."""
    out = dict(row)
    for col in table_cls.__table__.columns:
        name = col.name
        if name not in out or out[name] is None:
            continue
        col_type = str(col.type).lower()
        if isinstance(col.type, SAUuid) and isinstance(out[name], str):
            try:
                out[name] = uuid.UUID(out[name])
            except ValueError:
                pass
        elif "datetime" in col_type or name.endswith("_at"):
            if isinstance(out[name], str):
                try:
                    out[name] = datetime.fromisoformat(out[name])
                except ValueError:
                    pass
        # Enum columns: SQLAlchemy's Enum() will coerce the string back to the
        # Python enum at insert time, so leave the string alone.
    return out


async def _restore_table(db, cls, rows: list[dict[str, Any]], *, redact_warning: bool = False) -> int:
    inserted = 0
    pk_cols = [c for c in cls.__table__.primary_key.columns]
    for row in rows:
        coerced = _coerce(cls, row)
        # Skip if primary key already present.
        pk_filter = [pk == coerced[pk.name] for pk in pk_cols if pk.name in coerced]
        if pk_filter:
            existing = (await db.execute(select(cls).filter(*pk_filter))).first()
            if existing:
                continue
        if redact_warning and any(coerced.get(k) == REDACTED for k in ("access_token", "refresh_token")):
            print(f"  ! Skipping {cls.__tablename__} row with redacted secrets — reconnect via OAuth.")
            continue
        db.add(cls(**coerced))
        inserted += 1
    await db.commit()
    return inserted


async def main_async(path: Path, *, allow_non_empty: bool) -> int:
    raw = json.loads(path.read_text())
    if raw.get("schema_version") != "1":
        print(f"Unknown schema_version: {raw.get('schema_version')!r}", file=sys.stderr)
        return 2

    await init_schema()

    async with database.AsyncSessionLocal() as db:
        if not allow_non_empty:
            existing = (await db.execute(select(ScheduledPost).limit(1))).scalar_one_or_none()
            if existing is not None:
                print(
                    "Refusing to restore into a non-empty database. Re-run with --allow-non-empty "
                    "if you really want to merge.",
                    file=sys.stderr,
                )
                return 3

        plan = [
            (PlatformToken, raw.get("platform_tokens", []), True),
            (ScheduledPost, raw.get("scheduled_posts", []), False),
            (Draft, raw.get("drafts", []), False),
            (PostAnalytics, raw.get("post_analytics", []), False),
            (AppSetting, raw.get("app_settings", []), False),
            (SubstackSeen, raw.get("substack_seen", []), False),
        ]
        totals = {}
        for cls, rows, redact in plan:
            n = await _restore_table(db, cls, rows, redact_warning=redact)
            totals[cls.__tablename__] = n
        print(f"Restored: {totals}")
        print("Reminder: platform_tokens secrets are redacted in backups. Reconnect via the dashboard.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("backup", type=Path)
    ap.add_argument("--allow-non-empty", action="store_true")
    args = ap.parse_args()
    return asyncio.run(main_async(args.backup, allow_non_empty=args.allow_non_empty))


if __name__ == "__main__":
    raise SystemExit(main())
