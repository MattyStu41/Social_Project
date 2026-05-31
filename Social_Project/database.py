"""
Async SQLAlchemy engine + session factory.

Supports both Supabase Postgres and Neon Postgres. Connection pooling and
statement-level timeouts prevent a hung platform API call from holding a
pool slot forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Point it at your Supabase or Neon Postgres instance."
    )


def _engine_kwargs(url: str) -> dict[str, Any]:
    """Postgres gets production pool settings. SQLite (tests) does not."""
    if url.startswith("sqlite"):
        return {"echo": False, "future": True}

    # Supabase/Postgres connection settings
    kwargs: dict[str, Any] = {
        "echo": False,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 5,
        "max_overflow": 5,
    }

    # Supabase pools already handle SSL, and connection strings include parameters
    # Only add server_settings for non-Supabase Postgres
    if "supabase" not in url.lower():
        kwargs["connect_args"] = {
            "server_settings": {
                "application_name": "jack-social-scheduler",
                "statement_timeout": "30000",
            },
            "ssl": True,
        }

    return kwargs


engine = create_async_engine(settings.database_url, **_engine_kwargs(settings.database_url))

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_schema() -> None:
    """Initialize database schema.

    For Supabase: Schema is managed via Supabase migrations, so skip Alembic.
    For SQLite (tests): Use Base.metadata.create_all.
    For other Postgres (Neon/managed): Use Alembic migrations.
    """
    from models import PlatformToken, ScheduledPost  # noqa: F401  (registers metadata)

    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    # Supabase: migrations are handled externally via Supabase dashboard
    if "supabase" in settings.database_url.lower():
        return

    # Other Postgres (Neon, etc.): run Alembic migrations
    import asyncio

    from alembic.config import Config

    from alembic import command

    def _upgrade() -> None:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_upgrade)


# Kept as a deprecated shim so existing import sites do not break during the
# Alembic cutover. New code should call init_schema() instead.
create_tables = init_schema


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
