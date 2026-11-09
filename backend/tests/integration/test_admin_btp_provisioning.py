"""Isolated end-to-end authorization provisioning workflow."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.agents.single_agent import SingleAgent
from app.auth.permissions import RoleName
from app.integrations.deepseek_client import DeepSeekClient
from tests.support import create_tree, create_user, login, user_with_role


@dataclass
class FakeDeepSeek:
    requests: list[dict] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "Cloud Foundry is an application runtime."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


@pytest.fixture
async def deepseek(client: AsyncClient):
    app = client.app  # type: ignore[attr-defined]
    fake = FakeDeepSeek()
    settings = app.state.settings.model_copy(
        update={"DEEPSEEK_API_KEY": SecretStr("sk-test-provisioning"), "DEEPSEEK_MAX_RETRIES": 0}
    )
    mocked = DeepSeekClient(settings, transport=httpx.MockTransport(fake))
    app.state.agent = SingleAgent(mocked, app.state.tool_registry, app.state.redactor)
    yield fake
    await mocked.close()


async def test_platform_admin_registers_hierarchy_assigns_viewer_and_revocation_takes_effect(
    client: AsyncClient, deepseek
) -> None:
    admin, admin_headers = await user_with_role(
        client.app, client, "platform@example.com", RoleName.ADMINISTRATOR
    )
    target = await create_user(client.app, "viewer@example.com")

    empty = await client.get("/api/v1/admin/btp/scopes", headers=admin_headers)
    assert empty.status_code == 200
    assert empty.json() == {"accounts": [], "environments": []}

    global_account = await client.post(
        "/api/v1/admin/btp/accounts",
        json={"external_account_id": "ga-test", "name": "Test Global", "account_type": "global_account"},
        headers=admin_headers,
    )
    assert global_account.status_code == 201, global_account.text
    global_id = global_account.json()["id"]

    subaccount = await client.post(
        "/api/v1/admin/btp/accounts",
        json={
            "external_account_id": "sa-test",
            "name": "Test Subaccount",
            "account_type": "subaccount",
            "parent_account_id": global_id,
        },
        headers=admin_headers,
    )
    assert subaccount.status_code == 201, subaccount.text
    subaccount_id = subaccount.json()["id"]

    environment = await client.post(
        f"/api/v1/admin/btp/accounts/{subaccount_id}/environments",
        json={
            "name": "Development",
            "runtime_type": "cloudfoundry",
            "external_runtime_id": "cf-org-test",
        },
        headers=admin_headers,
    )
    assert environment.status_code == 201, environment.text
    environment_id = environment.json()["id"]

    # Mapping is administrative metadata only; it must not grant access implicitly.
    target_headers = await login(client, "viewer@example.com")
    before_grant = await client.get("/api/v1/auth/me", headers=target_headers)
    assert before_grant.json()["roles"] == []
    assert (await client.get("/api/v1/conversations", headers=target_headers)).status_code == 403

    assigned = await client.post(
        f"/api/v1/admin/users/{target.id}/grants",
        json={
            "role": "viewer",
            "scope_type": "environment",
            "scope_id": environment_id,
            "include_descendants": False,
            "expires_at": None,
        },
        headers=admin_headers,
    )
    assert assigned.status_code == 201, assigned.text

    refreshed = await client.get("/api/v1/auth/me", headers=target_headers)
    assert refreshed.status_code == 200
    assert refreshed.json()["roles"] == ["viewer"]
    assert "chat:use" in refreshed.json()["permissions"]
    assert "btp:read" in refreshed.json()["permissions"]

    conversation = await client.post("/api/v1/conversations", json={}, headers=target_headers)
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]
    message = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Explain SAP BTP.", "client_message_id": str(uuid.uuid4())},
        headers=target_headers,
    )
    assert message.status_code == 201, message.text
    reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{message.json()['id']}/reply",
        headers=target_headers,
    )
    assert reply.status_code == 201, reply.text
    assert deepseek.requests
    assert "Cloud Foundry is an application runtime." in reply.json()["message"]["content"]

    revoked = await client.post(
        f"/api/v1/admin/grants/{assigned.json()['id']}/revoke", headers=admin_headers
    )
    assert revoked.status_code == 200, revoked.text
    after_revoke = await client.get("/api/v1/auth/me", headers=target_headers)
    assert after_revoke.json()["roles"] == []
    assert (await client.get("/api/v1/conversations", headers=target_headers)).status_code == 403

    # The administrator itself is not allowed to assign a grant to itself.
    self_assign = await client.post(
        f"/api/v1/admin/users/{admin.id}/grants",
        json={
            "role": "viewer",
            "scope_type": "environment",
            "scope_id": environment_id,
            "include_descendants": False,
            "expires_at": None,
        },
        headers=admin_headers,
    )
    assert self_assign.status_code == 403


async def test_scope_registration_and_grants_are_restricted_to_platform_admin(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, viewer_headers = await user_with_role(
        client.app, client, "viewer@example.com", RoleName.VIEWER, tree.sub
    )
    denied = await client.get("/api/v1/admin/btp/scopes", headers=viewer_headers)
    assert denied.status_code == 403

    delegated, delegated_headers = await user_with_role(
        client.app, client, "delegated@example.com", RoleName.ADMINISTRATOR, tree.sub
    )
    target = await create_user(client.app, "target@example.com")
    cross_scope = await client.post(
        f"/api/v1/admin/users/{target.id}/grants",
        json={
            "role": "viewer",
            "scope_type": "environment",
            "scope_id": str(tree.other_dev.id),
            "include_descendants": False,
            "expires_at": None,
        },
        headers=delegated_headers,
    )
    assert cross_scope.status_code == 404
    assert delegated.id != target.id
