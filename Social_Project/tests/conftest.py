"""Pytest fixtures.

Sets the minimal env the SUT modules require **before** they are imported,
swaps the production engine for an in-memory SQLite engine with a single
shared connection (so async sessions see the same database), creates the
schema, and resets the circuit-breaker singleton between tests.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

# ---- Env must be set BEFORE config/database import ----
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("BASE_URL", "http://localhost:8000")
# Provide an admin hash + secret so auth_enabled=True and the protected
# routers behave as they do in production. The hash is for password "secret".
os.environ.setdefault(
    # bcrypt hash of "secret" — used by the TestClient login fixture.
    "ADMIN_PASSWORD_HASH",
    "$2b$12$R4iCKtri3Z.Hi6qJCuzuk.OFYKzZx4tLwnlmJHY5k8On6Mu6vVcZC",
)
os.environ.setdefault("SESSION_SECRET", "test-session-secret-do-not-use-in-prod")
os.environ.setdefault("META_APP_ID", "test-meta-id")
os.environ.setdefault("META_APP_SECRET", "test-meta-secret")
os.environ.setdefault("TIKTOK_CLIENT_KEY", "test-tt-key")
os.environ.setdefault("TIKTOK_CLIENT_SECRET", "test-tt-secret")
os.environ.setdefault("SCHEDULER_INTERVAL_SECONDS", "30")
# Tight breaker thresholds for fast tests.
os.environ.setdefault("CIRCUIT_BREAKER_THRESHOLD", "3")
os.environ.setdefault("CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30")
# Tests drive the scheduler logic directly; we do not need APScheduler to
# spin up its own background loop.
os.environ.setdefault("DISABLE_SCHEDULER", "1")


from datetime import UTC

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool


def _make_test_engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@pytest_asyncio.fixture
async def engine():
    """Per-test in-memory SQLite engine. Schema is created from Base metadata."""
    import database
    import models  # noqa: F401
    from database import Base

    eng = _make_test_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Swap the module-level engine and session factory so production code
    # paths (e.g. scheduler.run_due_posts) see the test database.
    original_engine = database.engine
    original_factory = database.AsyncSessionLocal
    database.engine = eng
    database.AsyncSessionLocal = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    try:
        yield eng
    finally:
        database.engine = original_engine
        database.AsyncSessionLocal = original_factory
        await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncIterator[AsyncSession]:
    import database

    async with database.AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def session_factory(engine):
    """Yields a callable that opens a fresh AsyncSession against the test engine.

    Use this when a test needs to read back state that was written by code
    running inside its own session (e.g. the scheduler), since the original
    `db_session` keeps stale objects in its identity map.
    """
    import database

    def _open():
        return database.AsyncSessionLocal()

    yield _open


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Each test gets a clean circuit breaker state."""
    from services.circuit_breaker import get_breaker

    get_breaker().reset()
    yield
    get_breaker().reset()


@pytest_asyncio.fixture
async def seeded_tokens(db_session):
    """Insert a token row per platform so the scheduler does not bail with
    'platform not connected'."""
    from datetime import datetime, timedelta

    from models import PlatformToken

    now = datetime.now(UTC)
    db_session.add_all(
        [
            PlatformToken(
                platform="threads",
                access_token="threads-tok",
                user_id="thr-1",
                account_label="@thr",
                expires_at=now + timedelta(days=30),
            ),
            PlatformToken(
                platform="instagram",
                access_token="ig-tok",
                user_id="ig-1",
                account_label="@ig",
                expires_at=now + timedelta(days=30),
            ),
            PlatformToken(
                platform="tiktok",
                access_token="tt-tok",
                refresh_token="tt-refresh",
                user_id="tt-1",
                account_label="@tt",
                expires_at=now + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()
    yield


def make_post(
    *,
    caption: str = "hello world",
    platforms=("threads",),
    media_url: str | None = None,
    media_type: str | None = None,
    scheduled_at=None,
    status=None,
    platform_results=None,
):
    """Helper for tests to build a ScheduledPost without going through the
    Pydantic schema (which enforces media requirements that some negative
    tests need to bypass)."""
    from datetime import datetime

    from models import PostStatus, ScheduledPost

    return ScheduledPost(
        id=uuid.uuid4(),
        caption=caption,
        platforms=list(platforms),
        media_url=media_url,
        media_type=media_type,
        scheduled_at=scheduled_at or datetime.now(UTC),
        status=status or PostStatus.pending,
        platform_results=platform_results or {},
        attempts=[],
    )
