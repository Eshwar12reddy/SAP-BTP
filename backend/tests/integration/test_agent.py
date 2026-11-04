"""The agent through the reply endpoint: routing, BTP scope, evidence and AI failures.

DeepSeek is replaced by httpx.MockTransport and every call is counted; nothing is billed.
No live BTP system exists; the "live" provider below is a test double that lets the
evidence path for a successful live check be verified.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, update

from app.agents.contracts import AgentContext
from app.agents.single_agent import SingleAgent
from app.auth.authorization import Principal, load_active_grants
from app.auth.permissions import RoleName
from app.integrations.deepseek_client import DeepSeekClient
from app.integrations.sap_btp.provider import BTPApplication
from app.models import AccessGrant, AuditEvent, BTPEnvironment, Message, User
from app.tools.btp import APPLICATIONS_TOOL
from app.tools.contracts import ToolStatus
from tests.support import create_tree, grant, user_with_role


@dataclass
class FakeDeepSeek:
    replies: list[httpx.Response] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)

    def answer(self, content: str) -> None:
        self.replies = [
            httpx.Response(
                200,
                json={
                    "model": "deepseek-flash",
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.replies[min(len(self.requests), len(self.replies)) - 1]


@pytest.fixture
async def deepseek(client: AsyncClient):
    """Swap the agent's DeepSeek client for a mocked one with a (fake) key."""
    app = client.app  # type: ignore[attr-defined]
    fake = FakeDeepSeek()
    fake.answer("Cloud Foundry is an application runtime.")
    settings = app.state.settings.model_copy(
        update={"DEEPSEEK_API_KEY": SecretStr("sk-test-agent"), "DEEPSEEK_MAX_RETRIES": 0}
    )
    mocked = DeepSeekClient(settings, transport=httpx.MockTransport(fake))
    app.state.agent = SingleAgent(mocked, app.state.tool_registry, app.state.redactor)
    yield fake
    await mocked.close()


@dataclass
class LiveProvider:
    """Test double for a future live adapter (verifies the success evidence path)."""

    name: str = "test-live"

    async def list_applications(self, environment: BTPEnvironment) -> list[BTPApplication]:
        return [BTPApplication(id="a1", name="orders-api", state="STARTED")]


async def ask(client: AsyncClient, headers: dict, text: str, conversation_id=None, **body):
    if conversation_id is None:
        created = await client.post("/api/v1/conversations", json=body, headers=headers)
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]
    sent = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": text, "client_message_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert sent.status_code == 201, sent.text
    message_id = sent.json()["id"]
    reply = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply", headers=headers
    )
    return reply, conversation_id, message_id


async def stored_messages(client: AsyncClient, conversation_id: str) -> list[Message]:
    async with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        rows = await session.scalars(
            select(Message).where(Message.conversation_id == uuid.UUID(conversation_id))
        )
        return list(rows)


# ---------- stored catalog ----------


async def test_catalog_lists_only_authorized_scope_as_stored_metadata(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)

    reply, _, message_id = await ask(client, headers, "Show my BTP accounts.")
    assert reply.status_code == 201, reply.text
    body = reply.json()
    content = body["message"]["content"]
    assert "Development (cloudfoundry)" in content
    for hidden in ("Acme EU", "Acme Global", "Testing", "Production", "Other", "Acme US"):
        assert hidden not in content
    assert "not a live SAP BTP check" in content
    assert body["route"] == "btp_account"
    assert body["sources"] == ["stored_metadata"]
    assert body["live_check_performed"] is False
    assert [t["status"] for t in body["tool_results"]] == ["success"]
    assert body["message"]["reply_to_message_id"] == message_id
    assert deepseek.requests == []  # rule-routed, rendered without the model


async def test_knowledge_reply_is_extractive_and_rechecked_after_access_revocation(
    client: AsyncClient, deepseek, monkeypatch
) -> None:
    class _OfflineEncoding:
        def encode(self, value: str, disallowed_special=()) -> list[int]:
            return [ord(character) for character in value]

        def decode(self, tokens: list[int]) -> str:
            return "".join(chr(token) for token in tokens)

        def decode_with_offsets(self, tokens: list[int]) -> tuple[str, list[int]]:
            return self.decode(tokens), list(range(len(tokens)))

    encoding = _OfflineEncoding()
    monkeypatch.setattr("app.rag.chunking._encoding", lambda _name: encoding)
    monkeypatch.setattr("app.services.retrieval_service._encoding", lambda _name: encoding)
    tree = await create_tree(client.app)
    user, manager = await user_with_role(
        client.app, client, "knowledge-chat@example.com", RoleName.KNOWLEDGE_MANAGER, tree.sub
    )
    await grant(client.app, user, RoleName.VIEWER, tree.other_sub, include_descendants=True)
    content = b"Credential rotation is performed after service binding updates."
    registered = await client.post(
        "/api/v1/knowledge/sources",
        data={"title": "Rotation runbook", "scope_type": "btp_account", "scope_id": str(tree.sub.id)},
        files={"file": ("rotation.md", content, "text/markdown")},
        headers=manager,
    )
    assert registered.status_code == 201, registered.text

    reply, conversation_id, message_id = await ask(
        client, manager, "Search knowledge base for credential rotation."
    )
    assert reply.status_code == 201, reply.text
    body = reply.json()
    assert body["route"] == "knowledge_search"
    assert "Credential rotation is performed" in body["message"]["content"]
    assert body["knowledge_citations"]
    assert deepseek.requests == []

    async with client.app.state.session_factory() as session:
        await session.execute(
            update(AccessGrant)
            .where(AccessGrant.user_id == user.id, AccessGrant.role_name == RoleName.KNOWLEDGE_MANAGER.value)
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
    messages = await client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=manager)
    assert messages.status_code == 200
    assistant = next(item for item in messages.json()["items"] if item["role"] == "assistant")
    assert "no longer available" in assistant["content"]
    assert "Credential rotation is performed" not in assistant["content"]
    status_response = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply", headers=manager
    )
    assert status_response.status_code == 200
    assert "no longer available" in status_response.json()["reply"]["message"]["content"]


# ---------- applications ----------


async def test_unavailable_live_integration_is_reported_never_invented(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app, client, "v@x.io", RoleName.VIEWER, tree.sub, include_descendants=True
    )
    reply, _, _ = await ask(client, headers, "Show me the apps in development.")
    body = reply.json()
    assert body["outcome"] == "tool_unavailable"
    assert body["live_check_performed"] is False
    assert body["sources"] == []
    statuses = [(t["tool_name"], t["status"], t["error_code"]) for t in body["tool_results"]]
    assert statuses == [
        ("btp_catalog_read", "success", None),
        (APPLICATIONS_TOOL, "unavailable", "btp_integration_unavailable"),
    ]
    content = body["message"]["content"]
    assert "Development (cloudfoundry) in Acme EU" in content
    assert "not configured" in content and "No live SAP BTP check was performed" in content
    assert deepseek.requests == []


async def test_ambiguous_environment_asks_for_clarification(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app, client, "v@x.io", RoleName.VIEWER, tree.sub, include_descendants=True
    )
    reply, _, _ = await ask(client, headers, "Which applications are running?")
    body = reply.json()
    assert body["outcome"] == "needs_clarification"
    assert [t["tool_name"] for t in body["tool_results"]] == ["btp_catalog_read"]
    for name in ("Development", "Testing", "Production"):
        assert name in body["message"]["content"]


async def test_unauthorized_environment_is_never_substituted(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, _, _ = await ask(
        client, headers, "List the applications in the production environment."
    )
    body = reply.json()
    assert body["outcome"] == "needs_clarification"
    assert "couldn't find a matching environment" in body["message"]["content"]
    assert [t["tool_name"] for t in body["tool_results"]] == ["btp_catalog_read"]


async def test_conversation_context_selects_the_environment(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(
        client.app, client, "v@x.io", RoleName.VIEWER, tree.sub, include_descendants=True
    )
    reply, _, _ = await ask(
        client, headers, "Which applications are running?", btp_environment_id=str(tree.test.id)
    )
    assert "Testing (cloudfoundry)" in reply.json()["message"]["content"]


async def test_successful_live_check_carries_evidence(client, deepseek) -> None:
    client.app.state.btp_provider = LiveProvider()
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, _, _ = await ask(client, headers, "Show me the apps in development.")
    body = reply.json()
    assert body["outcome"] == "answered"
    assert body["live_check_performed"] is True
    assert body["sources"] == ["live_btp"]
    assert body["tool_results"][1]["live_check_performed"] is True
    content = body["message"]["content"]
    assert "orders-api (STARTED)" in content
    assert "live SAP BTP data from the 'test-live' integration, retrieved" in content


async def test_registry_rejects_a_forged_out_of_scope_environment(client) -> None:
    app = client.app
    tree = await create_tree(app)
    user, _ = await user_with_role(app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    async with app.state.session_factory() as session:
        loaded = await session.get(User, user.id)
        principal = Principal(loaded, uuid.uuid4(), await load_active_grants(session, user.id))
        context = AgentContext(
            principal=principal,
            session=session,
            btp_provider=LiveProvider(),
            session_factory=app.state.session_factory,
        )
        result = await app.state.tool_registry.execute(
            APPLICATIONS_TOOL, {"environment_id": str(tree.other_dev.id)}, context
        )
    assert (result.status, result.error_code) == (ToolStatus.DENIED, "not_found_or_not_authorized")
    assert result.data is None and result.live_check_performed is False
    async with app.state.session_factory() as session:
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.event_type == "authz.access_denied",
            )
        )
    assert audit is not None and audit.outcome == "denied"
    assert audit.details["tool"] == APPLICATIONS_TOOL


# ---------- operations and model answers ----------


async def test_operation_request_is_refused_without_tools_or_model(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "o@x.io", RoleName.OPERATOR, tree.dev)
    reply, _, _ = await ask(client, headers, "Restart my application in development.")
    body = reply.json()
    assert body["outcome"] == "operation_not_performed"
    assert body["tool_results"] == []
    assert "Nothing was changed" in body["message"]["content"]
    assert deepseek.requests == []


async def test_model_answer_is_labelled_and_gets_no_btp_data(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, conversation_id, _ = await ask(
        client, headers, "What is Cloud Foundry?", btp_environment_id=str(tree.dev.id)
    )
    body = reply.json()
    assert reply.status_code == 201
    assert body["sources"] == ["model_knowledge"]
    assert body["model"] == "deepseek-flash"
    content = body["message"]["content"]
    assert content.startswith("Cloud Foundry is an application runtime.")
    assert content.endswith("No live SAP BTP system was checked.")
    assert len(deepseek.requests) == 1
    sent = json.dumps(deepseek.requests[0])
    assert "Never claim to have checked" in sent
    for private in ("Development", "Acme", str(tree.dev.id)):
        assert private not in sent

    # Idempotent: asking again returns the stored reply without another model call.
    message_id = body["message"]["reply_to_message_id"]
    again = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply", headers=headers
    )
    assert again.status_code == 200
    assert again.json()["message"]["id"] == body["message"]["id"]
    assert again.json()["sources"] == ["model_knowledge"]
    assert len(deepseek.requests) == 1


async def test_model_claiming_a_live_check_gets_a_correction(client, deepseek) -> None:
    deepseek.answer("I checked your account and your apps are healthy.")
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, _, _ = await ask(client, headers, "What is SAP BTP?")
    body = reply.json()
    assert "Anything that sounds like a live check is not verified." in body["message"]["content"]
    assert body["live_check_performed"] is False


async def test_deepseek_failure_stores_nothing(client, deepseek) -> None:
    deepseek.replies = [httpx.Response(503)]
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, conversation_id, _ = await ask(client, headers, "Explain SAP BTP.")
    assert reply.status_code == 503
    assert reply.json()["error"]["code"] == "deepseek_unavailable"
    roles = [m.role for m in await stored_messages(client, conversation_id)]
    assert roles == ["user"]


async def test_missing_key_fails_model_routes_only(client) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, _, _ = await ask(client, headers, "Explain SAP BTP.")
    assert (reply.status_code, reply.json()["error"]["code"]) == (503, "deepseek_not_configured")
    catalog, _, _ = await ask(client, headers, "Show my BTP accounts.")
    assert catalog.status_code == 201


# ---------- ownership ----------


async def test_reply_requires_ownership_and_a_user_message(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, owner = await user_with_role(client.app, client, "a@x.io", RoleName.VIEWER, tree.dev)
    _, other = await user_with_role(client.app, client, "b@x.io", RoleName.VIEWER, tree.dev)
    reply, conversation_id, message_id = await ask(client, owner, "Show my BTP accounts.")
    url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply"
    assert (await client.post(url, headers=other)).status_code == 404
    assistant_id = reply.json()["message"]["id"]
    assistant_url = f"/api/v1/conversations/{conversation_id}/messages/{assistant_id}/reply"
    assert (await client.post(assistant_url, headers=owner)).status_code == 404


# ---------- durable reply state ----------


async def set_reply_status(client: AsyncClient, message_id: str, status: str, age_seconds: int) -> None:
    async with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        await session.execute(
            update(Message)
            .where(Message.id == uuid.UUID(message_id))
            .values(
                reply_status=status,
                reply_status_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
            )
        )
        await session.commit()


async def message_status(client: AsyncClient, headers: dict, conversation_id: str, message_id: str):
    page = await client.get(f"/api/v1/conversations/{conversation_id}/messages", headers=headers)
    by_id = {m["id"]: m for m in page.json()["items"]}
    status = await client.get(
        f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply", headers=headers
    )
    assert status.status_code == 200, status.text
    return by_id[message_id]["reply_status"], status.json()


async def test_failed_reply_survives_reload_and_retry_completes_once(client, deepseek) -> None:
    deepseek.replies = [httpx.Response(503)]
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, conversation_id, message_id = await ask(client, headers, "Explain SAP BTP.")
    assert reply.status_code == 503

    listed, status = await message_status(client, headers, conversation_id, message_id)
    assert listed == "failed" and status == {"status": "failed", "reply": None}

    deepseek.answer("SAP BTP is a cloud platform.")
    url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply"
    retried = await client.post(url, headers=headers)
    assert retried.status_code == 201
    listed, status = await message_status(client, headers, conversation_id, message_id)
    assert listed == "completed" and status["status"] == "completed"
    assert status["reply"]["message"]["id"] == retried.json()["message"]["id"]

    again = await client.post(url, headers=headers)
    assert again.status_code == 200
    assert len(deepseek.requests) == 2  # one failed attempt, one success; never regenerated
    roles = sorted(m.role for m in await stored_messages(client, conversation_id))
    assert roles == ["assistant", "user"]


async def test_reply_in_progress_is_durable_and_pollable(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    created = await client.post("/api/v1/conversations", json={}, headers=headers)
    conversation_id = created.json()["id"]
    sent = await client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "Explain SAP BTP.", "client_message_id": str(uuid.uuid4())},
        headers=headers,
    )
    message_id = sent.json()["id"]
    url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply"

    listed, status = await message_status(client, headers, conversation_id, message_id)
    assert listed is None and status == {"status": "not_requested", "reply": None}

    # Another worker is generating this reply right now.
    await set_reply_status(client, message_id, "generating", age_seconds=5)
    busy = await client.post(url, headers=headers)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "reply_in_progress"
    assert busy.headers["Retry-After"] == "3"
    assert (await message_status(client, headers, conversation_id, message_id))[1]["status"] == (
        "generating"
    )
    assert deepseek.requests == []

    # That worker died: past the run limit the claim is abandoned and can be taken over.
    await set_reply_status(client, message_id, "generating", age_seconds=3600)
    listed, status = await message_status(client, headers, conversation_id, message_id)
    assert listed == "failed" and status["status"] == "failed"
    taken_over = await client.post(url, headers=headers)
    assert taken_over.status_code == 201
    assert len(deepseek.requests) == 1


async def test_reply_status_requires_ownership(client, deepseek) -> None:
    tree = await create_tree(client.app)
    _, owner = await user_with_role(client.app, client, "a@x.io", RoleName.VIEWER, tree.dev)
    _, other = await user_with_role(client.app, client, "b@x.io", RoleName.VIEWER, tree.dev)
    _, conversation_id, message_id = await ask(client, owner, "Show my BTP accounts.")
    url = f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply"
    assert (await client.get(url, headers=other)).status_code == 404


async def test_catalog_names_are_markdown_escaped(client, deepseek) -> None:
    tree = await create_tree(client.app)
    async with client.app.state.session_factory() as session:
        await session.execute(
            update(BTPEnvironment)
            .where(BTPEnvironment.id == tree.dev.id)
            .values(name="Dev *x* [link](javascript:alert(1))")
        )
        await session.commit()
    _, headers = await user_with_role(client.app, client, "v@x.io", RoleName.VIEWER, tree.dev)
    reply, _, _ = await ask(client, headers, "Show my BTP accounts.")
    content = reply.json()["message"]["content"]
    assert r"- Dev \*x\* \[link\](javascript:alert(1)) (cloudfoundry)" in content
