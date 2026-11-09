"""Human approval workflow tests; no executor or external SAP call is used."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.auth.permissions import RoleName
from app.models import AgentApproval
from tests.support import create_tree, user_with_role


async def _operation_request(client: AsyncClient, headers: dict[str, str]) -> tuple[str, str]:
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    saved = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Restart the orders application.", "client_message_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert saved.status_code == 201, saved.text
    message_id = saved.json()["id"]
    reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply", headers=headers
    )
    assert reply.status_code == 201, reply.text
    return conversation_id, reply.json()["message"]["content"]


async def test_operation_request_interrupt_is_durable_and_execute_is_truthful(
    client: AsyncClient,
) -> None:
    tree = await create_tree(client.app)
    user, headers = await user_with_role(client.app, client, "approval@x.io", RoleName.VIEWER, tree.dev)
    conversation_id, content = await _operation_request(client, headers)
    assert "paused for human approval" in content

    status = await client.get(f"/api/v1/conversations/{conversation_id}/investigation", headers=headers)
    assert status.status_code == 200, status.text
    approval = status.json()["pending_approval"]
    assert approval["status"] == "pending"
    assert approval["execution_available"] is False

    explained = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "explain_command"},
        headers=headers,
    )
    assert explained.status_code == 200, explained.text
    assert explained.json()["approval"]["status"] == "pending"

    executed = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "execute"},
        headers=headers,
    )
    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["approval"]["status"] == "execution_unavailable"
    assert "No infrastructure change was made" in body["message"]

    async with client.app.state.session_factory() as session:
        row = await session.scalar(select(AgentApproval).where(AgentApproval.user_id == user.id))
        assert row is not None
        assert row.canonical_args["executor"] == "unavailable"


async def test_approval_is_bound_to_owner_and_thread(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, owner_headers = await user_with_role(client.app, client, "owner-approval@x.io", RoleName.VIEWER, tree.dev)
    _, other_headers = await user_with_role(client.app, client, "other-approval@x.io", RoleName.VIEWER, tree.dev)
    conversation_id, _ = await _operation_request(client, owner_headers)
    status = await client.get(f"/api/v1/conversations/{conversation_id}/investigation", headers=owner_headers)
    approval_id = status.json()["pending_approval"]["id"]

    denied = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval_id}/decision",
        json={"decision": "cancel"},
        headers=other_headers,
    )
    assert denied.status_code in (403, 404)


async def _pending_approval(client: AsyncClient, headers: dict[str, str]) -> tuple[str, dict]:
    conversation_id, _ = await _operation_request(client, headers)
    status = await client.get(
        f"/api/v1/conversations/{conversation_id}/investigation", headers=headers
    )
    assert status.status_code == 200, status.text
    approval = status.json()["pending_approval"]
    assert approval["status"] == "pending"
    return conversation_id, approval


async def test_other_approach_keeps_proposal_pending_then_cancel_is_terminal(
    client: AsyncClient,
) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "alternative@x.io", RoleName.VIEWER, tree.dev)
    conversation_id, approval = await _pending_approval(client, headers)

    alternative = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "other_approach"},
        headers=headers,
    )
    assert alternative.status_code == 200, alternative.text
    assert alternative.json()["approval"]["status"] == "pending"
    assert "alternative" in alternative.json()["message"].lower()

    canceled = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "cancel"},
        headers=headers,
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["approval"]["status"] == "cancelled"
    investigation = await client.get(
        f"/api/v1/conversations/{conversation_id}/investigation", headers=headers
    )
    assert investigation.json()["status"] == "canceled"

    replay = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "cancel"},
        headers=headers,
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "approval_not_pending"


async def test_expired_approval_is_truthful_and_malformed_decision_is_rejected(
    client: AsyncClient,
) -> None:
    tree = await create_tree(client.app)
    user, headers = await user_with_role(client.app, client, "expired@x.io", RoleName.VIEWER, tree.dev)
    conversation_id, approval = await _pending_approval(client, headers)

    malformed = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "run_shell"},
        headers=headers,
    )
    assert malformed.status_code == 422

    async with client.app.state.session_factory() as session:
        row = await session.scalar(select(AgentApproval).where(AgentApproval.user_id == user.id))
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    expired = await client.post(
        f"/api/v1/conversations/{conversation_id}/investigation/approvals/{approval['id']}/decision",
        json={"decision": "execute"},
        headers=headers,
    )
    assert expired.status_code == 200, expired.text
    assert expired.json()["approval"]["status"] == "expired"
    assert "expired" in expired.json()["message"].lower()
