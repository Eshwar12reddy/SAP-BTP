"""Conversation ownership, legacy protection and BTP-context re-authorization."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import update

from app.auth.permissions import RoleName
from app.models import AccessGrant, Conversation
from tests.support import client_for, create_tree, create_user, grant, login, user_with_role

BASE = "/api/v1/conversations"


async def test_unauthenticated_requests_are_rejected(client: AsyncClient) -> None:
    for method, path in (("GET", BASE), ("POST", BASE), ("GET", f"{BASE}/{uuid.uuid4()}/messages")):
        assert (await client.request(method, path)).status_code == 401


async def test_users_only_see_their_own_conversations(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, alice = await user_with_role(client.app, client, "alice@example.com", RoleName.VIEWER, tree.sub)
    async with client_for(client.app) as other:
        _, bob = await user_with_role(client.app, other, "bob@example.com", RoleName.VIEWER, tree.sub)

    conversation = (await client.post(BASE, headers=alice)).json()
    message = {"content": "private", "client_message_id": str(uuid.uuid4())}
    assert (await client.post(f"{BASE}/{conversation['id']}/messages", json=message, headers=alice)).status_code == 201

    assert (await client.get(BASE, headers=bob)).json()["items"] == []
    for path in (f"{BASE}/{conversation['id']}", f"{BASE}/{conversation['id']}/messages"):
        response = await client.get(path, headers=bob)
        assert response.status_code == 404 and "private" not in response.text
    hijack = await client.post(
        f"{BASE}/{conversation['id']}/messages",
        json={"content": "injected", "client_message_id": str(uuid.uuid4())},
        headers=bob,
    )
    assert hijack.status_code == 404
    # Ownership cannot be supplied by the client.
    forged = await client.post(BASE, json={"owner_id": str(uuid.uuid4())}, headers=bob)
    assert forged.status_code == 422


async def test_legacy_conversations_stay_hidden(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    async with client.app.state.session_factory() as session:
        legacy = Conversation(title="Created before authentication")
        session.add(legacy)
        await session.commit()
    _, headers = await user_with_role(client.app, client, "anyone@example.com", RoleName.VIEWER, tree.sub)
    assert (await client.get(BASE, headers=headers)).json()["items"] == []
    assert (await client.get(f"{BASE}/{legacy.id}", headers=headers)).status_code == 404
    assert (await client.get(f"{BASE}/{legacy.id}/messages", headers=headers)).status_code == 404


async def test_conversation_context_requires_btp_read_on_that_scope(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "ctx@example.com", RoleName.VIEWER, tree.dev)
    ok = await client.post(BASE, json={"btp_environment_id": str(tree.dev.id)}, headers=headers)
    assert ok.status_code == 201 and ok.json()["btp_environment_id"] == str(tree.dev.id)
    for payload in ({"btp_environment_id": str(tree.prod.id)}, {"btp_account_id": str(tree.sub.id)}):
        assert (await client.post(BASE, json=payload, headers=headers)).status_code == 404
    both = {"btp_environment_id": str(tree.dev.id), "btp_account_id": str(tree.sub.id)}
    assert (await client.post(BASE, json=both, headers=headers)).status_code == 422


async def test_revoked_scope_locks_its_conversations(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user = await create_user(client.app, "revoked@example.com")
    await grant(client.app, user, RoleName.VIEWER, tree.sub)  # keeps chat access
    dev_grant = await grant(client.app, user, RoleName.VIEWER, tree.dev)
    headers = await login(client, "revoked@example.com")
    scoped = (await client.post(BASE, json={"btp_environment_id": str(tree.dev.id)}, headers=headers)).json()
    general = (await client.post(BASE, headers=headers)).json()

    async with client.app.state.session_factory() as session:
        await session.execute(update(AccessGrant).where(AccessGrant.id == dev_grant.id).values(revoked_at=datetime.now(UTC)))
        await session.commit()

    listed = {c["id"] for c in (await client.get(BASE, headers=headers)).json()["items"]}
    assert listed == {general["id"]}
    for path in (f"{BASE}/{scoped['id']}", f"{BASE}/{scoped['id']}/messages"):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403 and response.json()["error"]["code"] == "btp_scope_revoked"
    post = await client.post(
        f"{BASE}/{scoped['id']}/messages", json={"content": "x", "client_message_id": str(uuid.uuid4())}, headers=headers
    )
    assert post.status_code == 403
    # The record itself is preserved.
    async with client.app.state.session_factory() as session:
        assert await session.get(Conversation, uuid.UUID(scoped["id"])) is not None


async def test_chat_requires_chat_permission(client: AsyncClient) -> None:
    await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "admin-only@example.com", RoleName.ADMINISTRATOR)
    response = await client.get(BASE, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"
