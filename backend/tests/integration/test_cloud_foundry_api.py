"""Authorized Cloud Foundry API tests using an in-process mocked provider transport."""

from __future__ import annotations

import asyncio

import httpx
from httpx import AsyncClient

from app.auth.permissions import RoleName
from tests.support import create_tree, user_with_role

BTP = "/api/v1/btp"
RUNTIME = f"{BTP}/runtime"


def connection_payload(environment_id: str) -> dict[str, str]:
    return {
        "display_name": "Development CF",
        "btp_environment_id": environment_id,
        "api_endpoint": "https://api.example.test",
        "credential_reference": "env://cf-test",
        "authentication_method": "bearer_token",
    }


def provider_handler(requests: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.startswith("/v3/")
        assert request.headers["authorization"] == "Bearer test-only-secret"
        path = request.url.path
        if path == "/v3/organizations/org-1":
            body = {"guid": "org-1", "name": "Development", "created_at": None, "updated_at": None}
        elif path == "/v3/organizations":
            body = {"resources": [{"guid": "org-1", "name": "Development"}], "pagination": {"next": None}}
        elif path in {"/v3/spaces", "/v3/spaces/space-1"}:
            resource = {
                "guid": "space-1",
                "name": "Tools",
                "relationships": {"organization": {"data": {"guid": "org-1"}}},
            }
            body = resource if path.endswith("space-1") else {"resources": [resource], "pagination": {"next": None}}
        elif path in {"/v3/apps", "/v3/apps/app-1"}:
            resource = {
                "guid": "app-1",
                "name": "Operations API",
                "state": "STARTED",
                "lifecycle": {"type": "buildpack"},
                "relationships": {"space": {"data": {"guid": "space-1"}}},
            }
            body = resource if path.endswith("app-1") else {"resources": [resource], "pagination": {"next": None}}
        elif path == "/v3/apps/app-1/processes":
            resource = {
                "guid": "process-1",
                "type": "web",
                "state": "RUNNING",
                "instances": 1,
                "relationships": {"app": {"data": {"guid": "app-1"}}},
            }
            body = {"resources": [resource], "pagination": {"next": None}}
        elif path == "/v3/processes/process-1":
            body = {
                "guid": "process-1",
                "type": "web",
                "state": "RUNNING",
                "relationships": {"app": {"data": {"guid": "app-1"}}},
            }
        elif path == "/v3/processes/process-1/stats":
            body = {
                "resources": [
                    {
                        "type": "web",
                        "index": 0,
                        "state": "RUNNING",
                        "usage": {"cpu": 0.2, "mem": 123, "disk": 456, "time": "2026-11-04T00:00:00Z"},
                        "uptime": 60,
                    }
                ]
            }
        elif path == "/v3/service_instances":
            resource = {
                "guid": "service-1",
                "name": "Operations DB",
                "relationships": {"space": {"data": {"guid": "space-1"}}},
            }
            body = {"resources": [resource], "pagination": {"next": None}}
        elif path == "/v3/service_bindings":
            resource = {
                "guid": "binding-1",
                "type": "app",
                "relationships": {
                    "app": {"data": {"guid": "app-1"}},
                    "service_instance": {"data": {"guid": "service-1"}},
                },
                "data": {"credentials": {"password": "must-not-leak"}},
            }
            body = {"resources": [resource], "pagination": {"next": None}}
        elif path == "/v3/app_usage_events":
            body = {
                "resources": [
                    {
                        "guid": "event-1",
                        "app": {"guid": "app-1", "name": "Operations API"},
                        "process": {"type": "web"},
                        "state": {"current": "RUNNING", "previous": "CRASHED"},
                        "instance_count": {"current": 1},
                        "created_at": "2026-11-04T00:00:00Z",
                    }
                ],
                "pagination": {"next": None},
            }
        else:
            return httpx.Response(404, request=request)
        return httpx.Response(200, json=body, request=request)

    return handler


async def test_read_only_inventory_is_typed_and_redacts_binding_data(client: AsyncClient, monkeypatch) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app,
        client,
        "cf-reader@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    settings = client.app.state.settings
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    settings.BTP_CLOUD_FOUNDRY_SCOPE_MAP = {
        str(tree.dev.id): {"organization_ids": ["org-1"], "space_ids": ["space-1"]}
    }
    created = await client.post(
        f"{BTP}/connections", headers=headers, json=connection_payload(str(tree.dev.id))
    )
    assert created.status_code == 201, created.text
    selected = await client.put(
        f"{BTP}/environments/active",
        headers=headers,
        json={"connection_id": created.json()["id"]},
    )
    assert selected.status_code == 200, selected.text

    requests: list[httpx.Request] = []
    client.app.state.cloud_foundry_transport = httpx.MockTransport(provider_handler(requests))

    responses = await asyncio.gather(
        client.get(f"{RUNTIME}/organizations", headers=headers),
        client.get(f"{RUNTIME}/spaces", headers=headers),
        client.get(f"{RUNTIME}/applications", headers=headers),
        client.get(f"{RUNTIME}/applications/app-1/processes", headers=headers),
        client.get(f"{RUNTIME}/processes/process-1/stats", headers=headers),
        client.get(f"{RUNTIME}/service-instances", headers=headers),
        client.get(f"{RUNTIME}/applications/app-1/service-bindings", headers=headers),
        client.get(f"{RUNTIME}/applications/app-1/events", headers=headers),
    )
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert responses[0].json()["items"][0]["id"] == "org-1"
    assert responses[2].json()["items"][0]["display_name"] == "Operations API"
    assert responses[4].json()["items"][0]["memory_bytes"] == 123
    assert "must-not-leak" not in "".join(response.text for response in responses)
    assert all(request.method == "GET" and request.url.path.startswith("/v3/") for request in requests)


async def test_inventory_rechecks_active_connection_and_scope(client: AsyncClient, monkeypatch) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app,
        client,
        "cf-disabled@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    settings = client.app.state.settings
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    settings.BTP_CLOUD_FOUNDRY_SCOPE_MAP = {
        str(tree.dev.id): {"organization_ids": ["org-1"]}
    }
    created = await client.post(
        f"{BTP}/connections", headers=headers, json=connection_payload(str(tree.dev.id))
    )
    await client.put(
        f"{BTP}/environments/active",
        headers=headers,
        json={"connection_id": created.json()["id"]},
    )
    disabled = await client.post(
        f"{BTP}/connections/{created.json()['id']}/disable", headers=headers
    )
    assert disabled.status_code == 200
    response = await client.get(f"{RUNTIME}/organizations", headers=headers)
    assert response.status_code == 503
    assert "credential" not in response.text.lower()
    diagnosis = await client.get(f"{RUNTIME}/applications/app-1/diagnosis", headers=headers)
    assert diagnosis.status_code == 503
    assert "credential" not in diagnosis.text.lower()


async def test_diagnosis_returns_deterministic_findings_and_safe_evidence(
    client: AsyncClient, monkeypatch
) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app,
        client,
        "cf-diagnosis@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    settings = client.app.state.settings
    monkeypatch.setenv("BTP_CF_TEST_SECRET", "test-only-secret")
    settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    settings.BTP_VALIDATION_ALLOWED_HOSTS = ["api.example.test"]
    settings.BTP_CLOUD_FOUNDRY_SCOPE_MAP = {
        str(tree.dev.id): {"organization_ids": ["org-1"], "space_ids": ["space-1"]}
    }
    created = await client.post(
        f"{BTP}/connections", headers=headers, json=connection_payload(str(tree.dev.id))
    )
    assert created.status_code == 201
    selected = await client.put(
        f"{BTP}/environments/active",
        headers=headers,
        json={"connection_id": created.json()["id"]},
    )
    assert selected.status_code == 200
    requests: list[httpx.Request] = []
    client.app.state.cloud_foundry_transport = httpx.MockTransport(provider_handler(requests))

    response = await client.get(
        f"{RUNTIME}/applications/app-1/diagnosis",
        params=[("expected_service_instance_id", "service-1")],
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["state"] == "STARTED"
    assert body["summary"]["requested_instances"] == 1
    assert body["summary"]["running_instances"] == 1
    assert any(finding["rule_id"] == "recent_crash_event" for finding in body["findings"])
    assert body["root_cause_established"] is False
    assert body["ai_explanation"] is None
    assert any("logs are not available" in item for item in body["missing_information"])
    assert "must-not-leak" not in response.text
    assert all(request.method == "GET" and request.url.path.startswith("/v3/") for request in requests)
