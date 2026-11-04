"""Routing rules and model-classification fallback. DeepSeek is mocked; nothing is billed."""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.contracts import Route, RoutingMethod
from app.agents.router import parse_classification, route_by_rules, route_request
from app.core.config import load_settings
from app.integrations.deepseek_client import DeepSeekClient
from app.safety.redaction import Redactor


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("Explain SAP BTP.", Route.GENERAL_KNOWLEDGE),
        ("What is Cloud Foundry?", Route.GENERAL_KNOWLEDGE),
        ("Explain the difference between Cloud Foundry and Kyma.", Route.GENERAL_KNOWLEDGE),
        ("Explain destinations in BTP.", Route.DOCUMENTATION),
        ("What is a service instance?", Route.DOCUMENTATION),
        ("How do service bindings work?", Route.DOCUMENTATION),
        ("Show my BTP accounts.", Route.BTP_ACCOUNT),
        ("List my subaccounts.", Route.BTP_ACCOUNT),
        ("Show available development environments.", Route.BTP_ACCOUNT),
        ("Show me the apps in development.", Route.BTP_APPLICATIONS),
        ("Which applications are running?", Route.BTP_APPLICATIONS),
        ("List the applications in the production environment.", Route.BTP_APPLICATIONS),
        ("Are my apps healthy?", Route.BTP_APPLICATIONS),
        ("Suggest an architecture for a document-processing application.", Route.ARCHITECTURE),
        ("How should we deploy a scalable FastAPI service?", Route.ARCHITECTURE),
        ("Recommend an integration architecture.", Route.ARCHITECTURE),
        ("Estimate memory requirements.", Route.SIZING),
        ("How many instances might we need?", Route.SIZING),
        ("Recommend sizing for 10,000 requests per day.", Route.SIZING),
        ("Restart my application.", Route.OPERATION_REQUEST),
        ("Increase the memory to 2 GB.", Route.OPERATION_REQUEST),
        ("Deploy the application.", Route.OPERATION_REQUEST),
        ("Delete a service instance.", Route.OPERATION_REQUEST),
        ("Please restart the app in production", Route.OPERATION_REQUEST),
        ("Can you scale my app to 3 instances?", Route.OPERATION_REQUEST),
        ("Go ahead and delete the destination", Route.OPERATION_REQUEST),
    ],
)
def test_rules_route_supported_requests(text: str, route: Route) -> None:
    assert route_by_rules(text) is route


@pytest.mark.parametrize("text", ["hello there", "do the thing", "xyz"])
def test_unclear_requests_match_no_rule(text: str) -> None:
    assert route_by_rules(text) is None


@pytest.mark.parametrize(
    ("output", "route"),
    [
        ('{"route": "BTP_ACCOUNT"}', Route.BTP_ACCOUNT),
        ("```json\n" + '{"route": "sizing"}' + "\n```", Route.SIZING),
        ('{"route": "OPERATION_REQUEST"}', Route.OPERATION_REQUEST),
        ('{"route": "delete_service_instance"}', Route.UNSUPPORTED),
        ('{"route": "BTP_APPLICATIONS", "tool": "btp_delete"}', Route.UNSUPPORTED),
        ('{"route": "BTP_ACCOUNT", "approved": true}', Route.UNSUPPORTED),
        ("BTP_ACCOUNT", Route.UNSUPPORTED),
        ("I think it is GENERAL_KNOWLEDGE", Route.UNSUPPORTED),
        ("", Route.UNSUPPORTED),
    ],
)
def test_model_classification_must_satisfy_the_contract(output: str, route: Route) -> None:
    assert parse_classification(output) is route


def _client(reply: str, calls: list[dict]) -> DeepSeekClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
            },
        )

    settings = load_settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        JWT_SECRET_KEY="router-test-jwt-secret-0123456789-abcdefgh",
        DEEPSEEK_API_KEY="sk-test-router",
    )
    return DeepSeekClient(settings, transport=httpx.MockTransport(handler))


async def test_rule_routing_makes_no_model_call() -> None:
    calls: list[dict] = []
    async with _client("UNUSED", calls) as client:
        decision = await route_request("Restart my application.", client, Redactor())
    assert (decision.route, decision.method) == (Route.OPERATION_REQUEST, RoutingMethod.RULE)
    assert calls == []


async def test_unclear_request_uses_one_validated_model_classification() -> None:
    calls: list[dict] = []
    async with _client('{"route": "BTP_ACCOUNT"}', calls) as client:
        decision = await route_request("hello there", client, Redactor())
    assert (decision.route, decision.method) == (Route.BTP_ACCOUNT, RoutingMethod.MODEL)
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 32
    data = calls[0]["messages"][1]["content"]
    assert data.startswith('<untrusted_content source="user_message"')
    assert "\nhello there\n" in data


async def test_invented_model_label_becomes_unsupported() -> None:
    async with _client('{"route": "run_shell_command"}', []) as client:
        decision = await route_request("do the thing", client, Redactor())
    assert decision.route is Route.UNSUPPORTED
