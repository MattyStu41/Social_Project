"""End-to-end check that /api/admin/alerts surfaces the same alerts the
scheduler job logs (Deliverable 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from models import PlatformToken


@pytest.fixture
def client(engine):
    # Lazy import so the test engine swap is already in place.
    from main import app

    with TestClient(app) as c:
        # Sign in (test password is "secret" — see conftest).
        r = c.post("/api/auth/login", json={"password": "secret"})
        assert r.status_code == 200, r.text
        yield c


@pytest.mark.asyncio
async def test_alerts_endpoint_returns_expiry_warning(client, db_session):
    # Seed a token expiring in 3 days → should produce a warning.
    db_session.add(
        PlatformToken(
            platform="threads",
            access_token="t",
            user_id="u",
            account_label="@u",
            expires_at=datetime.now(UTC) + timedelta(days=3),
        )
    )
    await db_session.commit()

    r = client.get("/api/admin/alerts")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    keys = {a["key"] for a in body["alerts"]}
    # D16: alert keys are scoped per-account (`threads:<uuid>_expiring_soon`).
    assert any(k.startswith("threads:") and k.endswith("_expiring_soon") for k in keys)


@pytest.mark.asyncio
async def test_alerts_endpoint_requires_auth():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        r = c.get("/api/admin/alerts")
        assert r.status_code == 401
