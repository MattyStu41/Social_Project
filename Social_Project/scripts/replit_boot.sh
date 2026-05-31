#!/usr/bin/env bash
# Replit boot entry point for JACK Social Scheduler.
# Idempotent: safe to re-run.
#
# Flow:
#   1. Install deps from requirements.txt (cached after first run).
#   2. Stamp Alembic head if the DB has existing tables but no alembic_version
#      (first boot after Alembic was added).
#   3. uvicorn binds 0.0.0.0:8000 (Replit external 80).
#
# APScheduler runs in-process. On free-tier Replit the Repl sleeps when idle;
# scheduled posts will not publish during sleep. Use Reserved VM for 24/7.
# See docs/DEPLOY_REPLIT.md.

set -euo pipefail

PORT="${PORT:-8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "[boot] Installing dependencies"
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

echo "[boot] Checking Alembic stamp"
python - <<'PYTHON'
import asyncio
import os

from sqlalchemy import inspect, create_engine

url = os.environ["DATABASE_URL"]
sync_url = url.replace("postgresql+asyncpg://", "postgresql://", 1).replace("postgres://", "postgresql://", 1)
eng = create_engine(sync_url)
with eng.connect() as conn:
    insp = inspect(conn)
    has_legacy = insp.has_table("scheduled_posts")
    has_alembic = insp.has_table("alembic_version")

if has_legacy and not has_alembic:
    print("[boot] Existing schema detected without alembic_version; stamping head.")
    from alembic import command
    from alembic.config import Config
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.stamp(cfg, "head")
PYTHON

echo "[boot] uvicorn main:app on 0.0.0.0:${PORT}"
exec uvicorn main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --proxy-headers \
    --forwarded-allow-ips='*'
