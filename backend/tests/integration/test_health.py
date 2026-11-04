from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import URL

from app.auth.tokens import create_access_token
from app.main import create_app
from tests.conftest import make_settings


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_when_database_and_schema_available(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}


async def test_readiness_fails_when_schema_is_not_at_expected_revision(client: AsyncClient) -> None:
    client.app.state.expected_schema_revision = "not-the-head"  # type: ignore[attr-defined]
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "database": "schema_not_ready"}


async def _outage_client(url: URL):
    app = create_app(make_settings(url, DATABASE_CONNECT_TIMEOUT=1, DATABASE_POOL_TIMEOUT=2))
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_database_unreachable(clean_database: URL) -> None:
    # Nothing listens on port 1: a safe, simulated outage that leaves PostgreSQL untouched.
    app, client = await _outage_client(clean_database.set(port=1))
    # A correctly signed token gets past JWT validation, so the session lookup must hit
    # the (unreachable) database: that path has to answer 503, not 401 or 500.
    token, _ = create_access_token(
        make_settings(clean_database), user_id=uuid.uuid4(), session_id=uuid.uuid4()
    )
    client.headers["Authorization"] = f"Bearer {token}"
    async with app.router.lifespan_context(app), client:
        live = await client.get("/health/live")
        ready = await client.get("/health/ready")
        create = await client.post("/api/v1/conversations")
        listing = await client.get("/api/v1/conversations")
        message = await client.post(
            f"/api/v1/conversations/{uuid.uuid4()}/messages",
            json={"content": "hi", "client_message_id": str(uuid.uuid4())},
        )

    assert live.status_code == 200
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "database": "unavailable"}
    for response in (create, listing, message):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_unavailable"
        # No driver messages, hosts or SQL leak to clients.
        assert "psycopg" not in response.text.lower()
        assert "5444" not in response.text and "port 1" not in response.text


async def test_database_authentication_failure(clean_database: URL) -> None:
    app, client = await _outage_client(clean_database.set(password="definitely-wrong"))
    async with app.router.lifespan_context(app), client:
        ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["database"] == "unavailable"
    assert "definitely-wrong" not in ready.text
