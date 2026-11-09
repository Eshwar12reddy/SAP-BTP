"""LangGraph workflow tests; all DeepSeek and BTP calls are mocked."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

import httpx
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from app.agents.orchestrator import AgentOrchestrator
from app.agents.state import AgentGraphState, InvestigationStatus
from app.auth.permissions import RoleName
from app.integrations.deepseek_client import DeepSeekClient
from tests.support import create_tree, user_with_role


@dataclass
class FakeDeepSeek:
    requests: list[dict] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "BTP is a cloud platform."}, "finish_reason": "stop"}],
            },
        )


async def test_graph_backed_chat_persists_completed_state_and_deduplicates(
    client: AsyncClient,
) -> None:
    tree = await create_tree(client.app)
    user, headers = await user_with_role(client.app, client, "graph@x.io", RoleName.VIEWER, tree.dev)
    fake = FakeDeepSeek()
    settings = client.app.state.settings.model_copy(
        update={"DEEPSEEK_API_KEY": SecretStr("sk-test-graph"), "DEEPSEEK_MAX_RETRIES": 0}
    )
    deepseek = DeepSeekClient(settings, transport=httpx.MockTransport(fake))
    client.app.state.agent = AgentOrchestrator(
        deepseek,
        client.app.state.tool_registry,
        client.app.state.redactor,
        checkpointer=InMemorySaver(),
        settings=settings,
    )
    try:
        created = await client.post("/api/v1/conversations", json={}, headers=headers)
        conversation_id = created.json()["id"]
        sent = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "What is SAP BTP?", "client_message_id": str(uuid.uuid4())},
            headers=headers,
        )
        message_id = sent.json()["id"]
        reply = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/{message_id}/reply",
            headers=headers,
        )
        assert reply.status_code == 201, reply.text
        assert len(fake.requests) == 1

        thread_id = client.app.state.agent.thread_id(
            user.id, uuid.UUID(conversation_id)
        )
        snapshot = await client.app.state.agent._graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        state = AgentGraphState.model_validate(snapshot.values)
        assert state.status is InvestigationStatus.COMPLETED
        assert "classify_intent" in {step.value for step in state.completed_steps}
        assert state.final_response_reference == f"conversation:{conversation_id}"

        sent_again = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "Explain Cloud Foundry.", "client_message_id": str(uuid.uuid4())},
            headers=headers,
        )
        message_again = sent_again.json()["id"]
        reply_again = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/{message_again}/reply",
            headers=headers,
        )
        assert reply_again.status_code == 201, reply_again.text
        assert len(fake.requests) == 2
        second_prompt = json.dumps(fake.requests[1])
        assert "What is SAP BTP?" in second_prompt
        assert "Explain Cloud Foundry." in second_prompt
    finally:
        await deepseek.close()
