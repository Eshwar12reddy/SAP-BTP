"""Phase 11 BTP connection management and authorization tests."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app.auth.permissions import RoleName
from app.models import AuditEvent
from app.services.audit_service import AuditEventType
from tests.support import create_tree, user_with_role

BTP = "/api/v1/btp"


def payload(environment_id: str) -> dict[str, str]:
    return {
        "display_name": "Development CF",
        "btp_environment_id": environment_id,
        "api_endpoint": "https://api.example.test",
        "credential_reference": "env://cf-test",
        "authentication_method": "bearer_token",
    }


async def test_operator_can_register_read_validate_select_and_disable_connection(
    client: AsyncClient,
) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app,
        client,
        "operator@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    client.app.state.settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}

    created = await client.post(f"{BTP}/connections", headers=headers, json=payload(str(tree.dev.id)))
    assert created.status_code == 201, created.text
    connection = created.json()
    assert connection["credential_configured"] is True
    assert "credential_reference" not in connection
    assert "BTP_CF_TEST_SECRET" not in created.text

    listed = await client.get(f"{BTP}/connections", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [connection["id"]]

    configuration = await client.post(
        f"{BTP}/connections/{connection['id']}/validate",
        headers=headers,
        json={"live": False},
    )
    assert configuration.status_code == 200, configuration.text
    assert configuration.json()["result"] == "valid_configuration"

    selected = await client.put(
        f"{BTP}/environments/active",
        headers=headers,
        json={"connection_id": connection["id"]},
    )
    assert selected.status_code == 200, selected.text
    active = await client.get(f"{BTP}/environments/active", headers=headers)
    assert active.status_code == 200
    assert active.json()["connection"]["id"] == connection["id"]

    disabled = await client.post(
        f"{BTP}/connections/{connection['id']}/disable", headers=headers
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert (
        await client.get(f"{BTP}/environments/active", headers=headers)
    ).json()["connection"] is None

    cannot_select = await client.put(
        f"{BTP}/environments/active",
        headers=headers,
        json={"connection_id": connection["id"]},
    )
    assert cannot_select.status_code == 400
    available = await client.get(f"{BTP}/environments/available", headers=headers)
    assert available.status_code == 200
    assert next(item for item in available.json()["items"] if item["id"] == str(tree.dev.id))["status"] == "disabled"

    async with client.app.state.session_factory() as session:
        events = set(
            await session.scalars(
                select(AuditEvent.event_type).where(
                    AuditEvent.btp_environment_id == tree.dev.id
                )
            )
        )
    assert AuditEventType.BTP_CONNECTION_REGISTERED.value in events
    assert AuditEventType.BTP_CONNECTION_VALIDATION_SUCCEEDED.value in events
    assert AuditEventType.BTP_ENVIRONMENT_SELECTED.value in events
    assert AuditEventType.BTP_CONNECTION_DISABLED.value in events


async def test_connection_scope_and_revision_conflicts_are_enforced(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, owner_headers = await user_with_role(
        client.app,
        client,
        "owner@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    _, outsider_headers = await user_with_role(
        client.app,
        client,
        "outsider@example.com",
        RoleName.OPERATOR,
        tree.other_sub,
        include_descendants=True,
    )
    client.app.state.settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}

    created = await client.post(
        f"{BTP}/connections", headers=owner_headers, json=payload(str(tree.dev.id))
    )
    assert created.status_code == 201
    connection = created.json()

    hidden = await client.get(f"{BTP}/connections/{connection['id']}", headers=outsider_headers)
    assert hidden.status_code == 404
    forbidden_create = await client.post(
        f"{BTP}/connections", headers=outsider_headers, json=payload(str(tree.dev.id))
    )
    assert forbidden_create.status_code == 404

    conflict = await client.patch(
        f"{BTP}/connections/{connection['id']}",
        headers=owner_headers,
        json={"revision": 999, "display_name": "Stale"},
    )
    assert conflict.status_code == 409

    invalid = await client.post(
        f"{BTP}/connections",
        headers=owner_headers,
        json={**payload(str(tree.test.id)), "api_endpoint": "http://localhost:8080"},
    )
    assert invalid.status_code == 400


async def test_live_validation_is_server_disabled_by_default(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app,
        client,
        "live@example.com",
        RoleName.OPERATOR,
        tree.sub,
        include_descendants=True,
    )
    client.app.state.settings.BTP_CREDENTIAL_ENV_MAP = {"cf-test": "BTP_CF_TEST_SECRET"}
    created = await client.post(
        f"{BTP}/connections", headers=headers, json=payload(str(tree.dev.id))
    )
    assert created.status_code == 201
    result = await client.post(
        f"{BTP}/connections/{created.json()['id']}/validate",
        headers=headers,
        json={"live": True},
    )
    assert result.status_code == 503
    assert "credential" not in result.text.lower()
