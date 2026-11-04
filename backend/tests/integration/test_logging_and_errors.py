from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.permissions import RoleName
from app.core.config import Settings
from app.main import create_app
from tests.support import PASSWORD, create_tree, create_user, grant


def json_logs(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


@asynccontextmanager
async def logged_client(settings: Settings):
    # Entered inside each test body so the log handler binds to capsys' stdout.
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail that must not reach clients")

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="https://testserver") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_request_logs_are_structured_and_exclude_message_content(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "super secret message body"
    async with logged_client(settings) as client:
        tree = await create_tree(client.app)
        user = await create_user(client.app, "logger@example.com")
        await grant(client.app, user, RoleName.VIEWER, tree.sub)
        login = await client.post(
            "/api/v1/auth/login", json={"email": "logger@example.com", "password": PASSWORD}
        )
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        conversation = (await client.post("/api/v1/conversations")).json()
        response = await client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": secret, "client_message_id": str(uuid.uuid4())},
        )
    assert response.status_code == 201

    output = capsys.readouterr().out
    assert secret not in output
    assert PASSWORD not in output
    assert login.json()["access_token"] not in output
    assert conversation["id"] not in output  # route templates, not raw paths

    request_logs = [r for r in json_logs(output) if r.get("event") == "http_request"]
    message_log = next(r for r in request_logs if r["method"] == "POST" and r["route"].endswith("/messages"))
    assert message_log["route"] == "/api/v1/conversations/{conversation_id}/messages"
    assert message_log["status_code"] == 201
    assert message_log["request_id"] == response.headers["x-request-id"]
    assert isinstance(message_log["duration_ms"], float)
    assert {"timestamp", "level", "message"} <= message_log.keys()


async def test_startup_logs_do_not_contain_credentials(settings: Settings, capsys) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        pass
    output = capsys.readouterr().out
    events = {r.get("event") for r in json_logs(output)}
    assert {"startup", "database_ready", "shutdown"} <= events
    password = settings.database_url.password
    assert password and password not in output


async def test_incoming_request_id_is_honoured_only_when_safe(settings: Settings) -> None:
    async with logged_client(settings) as client:
        good = await client.get("/health/live", headers={"X-Request-ID": "trace-abc_123"})
        bad = await client.get("/health/live", headers={"X-Request-ID": "evil value!" * 10})
    assert good.headers["x-request-id"] == "trace-abc_123"
    assert bad.headers["x-request-id"] != "evil value!" * 10
    assert len(bad.headers["x-request-id"]) == 32


async def test_unhandled_exception_returns_safe_500(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    async with logged_client(settings) as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert "internal detail" not in response.text
    assert "Traceback" not in response.text

    logs = json_logs(capsys.readouterr().out)
    failure = next(r for r in logs if r.get("event") == "unhandled_exception")
    assert failure["request_id"] == response.headers["x-request-id"]
    assert "RuntimeError" in failure["exception"]


async def test_cors_allows_only_configured_origins(settings: Settings) -> None:
    async with logged_client(settings) as client:
        allowed = await client.get("/health/live", headers={"Origin": "http://localhost:5173"})
        blocked = await client.get("/health/live", headers={"Origin": "https://evil.example"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers
