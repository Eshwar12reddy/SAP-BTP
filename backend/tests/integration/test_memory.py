"""Long-term memory persistence, retrieval and scope isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import update

from app.auth.permissions import RoleName
from app.models import AccessGrant, Memory, Message
from tests.support import create_tree, user_with_role


async def test_memory_crud_deduplicates_redacts_and_forgets(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user, headers = await user_with_role(client.app, client, "memory-one@example.com", RoleName.VIEWER, tree.sub)
    saved = await client.post(
        "/api/v1/memories",
        json={"category": "preference", "content": "Prefer concise operational summaries."},
        headers=headers,
    )
    assert saved.status_code == 201, saved.text
    memory_id = saved.json()["id"]
    assert saved.json()["verification_status"] == "user_confirmed"

    duplicate = await client.post(
        "/api/v1/memories",
        json={"category": "preference", "content": "password=do-not-store-this"},
        headers=headers,
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["content"] == "password=[REDACTED]"
    # A different redacted value is not a duplicate of the first preference.
    assert duplicate.json()["id"] != memory_id

    updated = await client.patch(
        f"/api/v1/memories/{memory_id}",
        json={"revision": 1, "content": "Prefer concise incident summaries."},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert (await client.get("/api/v1/memories", headers=headers)).json()["has_more"] is False

    forgotten = await client.delete(f"/api/v1/memories/{memory_id}", headers=headers)
    assert forgotten.status_code == 204
    assert (await client.get(f"/api/v1/memories/{memory_id}", headers=headers)).status_code == 404
    assert user.id


async def test_memory_isolated_between_users_and_revoked_scopes(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    user, owner_headers = await user_with_role(
        client.app, client, "memory-owner@example.com", RoleName.VIEWER, tree.sub
    )
    _, other_headers = await user_with_role(
        client.app, client, "memory-other@example.com", RoleName.VIEWER, tree.sub
    )
    conversation = await client.post(
        "/api/v1/conversations",
        json={"btp_account_id": str(tree.sub.id)},
        headers=owner_headers,
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = uuid.UUID(conversation.json()["id"])
    async with client.app.state.session_factory() as session:
        question = Message(
            conversation_id=conversation_id,
            role="user",
            content="Show my BTP accounts.",
            client_message_id=uuid.uuid4(),
            reply_status="completed",
            reply_status_at=datetime.now(UTC),
        )
        session.add(question)
        await session.flush()
        session.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content="The authorized catalog was read.",
                reply_to_message_id=question.id,
                agent_metadata={
                    "sources": ["stored_metadata"],
                    "tool_results": [{"tool_name": "catalog", "status": "success"}],
                },
            )
        )
        await session.commit()
    saved = await client.post(
        "/api/v1/memories",
        json={
            "category": "verified_finding",
            "content": "The authorized investigation observed a healthy deployment.",
            "source_conversation_id": str(conversation_id),
            "source_evidence": ["tool:catalog:success"],
        },
        headers=owner_headers,
    )
    assert saved.status_code == 201, saved.text
    memory_id = saved.json()["id"]
    assert saved.json()["verification_status"] == "confirmed"
    assert saved.json()["btp_account_id"] == str(tree.sub.id)

    assert (await client.get(f"/api/v1/memories/{memory_id}", headers=other_headers)).status_code == 404
    async with client.app.state.session_factory() as session:
        await session.execute(
            update(AccessGrant)
            .where(AccessGrant.user_id == user.id)
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    assert (await client.get(f"/api/v1/memories/{memory_id}", headers=owner_headers)).status_code == 403
    assert (await client.get("/api/v1/memories", headers=owner_headers)).status_code == 403


async def test_memory_expiry_and_missing_verified_source_are_safe(client: AsyncClient) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app, client, "memory-expiry@example.com", RoleName.VIEWER, tree.sub
    )
    rejected = await client.post(
        "/api/v1/memories",
        json={"category": "verified_finding", "content": "hypothesis only"},
        headers=headers,
    )
    assert rejected.status_code == 400

    saved = await client.post(
        "/api/v1/memories",
        json={
            "category": "preference",
            "content": "Temporary preference",
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    assert saved.status_code == 201, saved.text
    async with client.app.state.session_factory() as session:
        await session.execute(
            update(Memory)
            .where(Memory.id == uuid.UUID(saved.json()["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await client.get("/api/v1/memories", headers=headers)).json()["items"] == []
