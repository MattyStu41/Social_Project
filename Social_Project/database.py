"""
Async SQLAlchemy engine + session factory bound to Neon Postgres.

Neon idles connections aggressively, so pool_pre_ping is on and pool_recycle
keeps connections fresh. Statement-level timeouts prevent a hung Meta call
from holding a pool slot forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at your Neon Postgres instance "
        "(postgresql://USER:PASS@HOST/DB?sslmode=require)."
    )


def _engine_kwargs(url: str) -> dict:
    """Postgres gets the production pool + SSL. SQLite (tests) does not."""
    if url.startswith("sqlite"):
        return {"echo": False, "future": True}
    return {
        "echo": False,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 5,
        "max_overflow": 5,
        "connect_args": {
            "server_settings": {
                "application_name": "jack-social-scheduler",
                "statement_timeout": "30000",
            },
            "ssl": True,
        },
    }


engine = create_async_engine(settings.database_url, **_engine_kwargs(settings.database_url))

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_schema() -> None:
    """Apply Alembic migrations on boot. On a brand-new DB, this also creates
    the tables (migration 001 is the baseline). On an existing DB whose tables
    were created by the pre-Alembic create_all path, stamp it head first using
    `alembic stamp head` — see docs/DEPLOY_REPLIT.md.

    Falls back to Base.metadata.create_all when running in test mode against
    SQLite — Alembic is overkill for in-memory test databases.
    """
    from models import PlatformToken, ScheduledPost  # noqa: F401  (registers metadata)

    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    # Production path: run Alembic upgrade head in a thread (Alembic is sync).
    import asyncio

    from alembic import command
    from alembic.config import Config

    def _upgrade() -> None:
        cfg = Config("alembic.ini")
        # ConfigParser treats "%" as interpolation syntax, so a DB URL whose
        # password contains a literal "%" would crash set_main_option. Escape
        # it. (env.py reads the raw DATABASE_URL from os.environ anyway; this
        # value is only the fallback.)
        cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)


# Kept as a deprecated shim so existing import sites do not break during the
# Alembic cutover. New code should call init_schema() instead.
create_tables = init_schema


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
