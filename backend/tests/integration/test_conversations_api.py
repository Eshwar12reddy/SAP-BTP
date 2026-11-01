from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/v1/conversations"


async def create(client: AsyncClient, **body) -> dict:
    response = await client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def post_message(client: AsyncClient, conversation_id: str, content: str, client_id: str | None = None):
    return await client.post(
        f"{BASE}/{conversation_id}/messages",
        json={"content": content, "client_message_id": client_id or str(uuid.uuid4())},
    )


# ---------- conversations ----------


async def test_create_conversation_with_default_title(client: AsyncClient) -> None:
    response = await client.post(BASE)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New conversation"
    uuid.UUID(body["id"])
    assert body["created_at"].endswith("Z") or "+" in body["created_at"]  # timezone-aware


async def test_create_conversation_with_title_normalizes_whitespace(client: AsyncClient) -> None:
    body = await create(client, title="  Quota   review \n")
    assert body["title"] == "Quota review"


@pytest.mark.parametrize("payload", [{"title": "   "}, {"title": "x" * 201}, {"unexpected": 1}, {"title": "a\x00b"}])
async def test_create_conversation_validation(client: AsyncClient, payload: dict) -> None:
    response = await client.post(BASE, json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_get_conversation_and_missing_records(client: AsyncClient) -> None:
    created = await create(client, title="Lookup")
    response = await client.get(f"{BASE}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created

    missing = await client.get(f"{BASE}/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    malformed = await client.get(f"{BASE}/not-a-uuid")
    assert malformed.status_code == 422


async def test_list_orders_by_recent_activity_and_paginates(client: AsyncClient) -> None:
    first = await create(client, title="first")
    second = await create(client, title="second")
    third = await create(client, title="third")
    # Activity on the oldest conversation moves it to the top.
    assert (await post_message(client, first["id"], "bump")).status_code == 201

    page = (await client.get(BASE, params={"limit": 2})).json()
    assert [c["id"] for c in page["items"]] == [first["id"], third["id"]]
    assert page["has_more"] is True

    rest = (await client.get(BASE, params={"limit": 2, "offset": 2})).json()
    assert [c["id"] for c in rest["items"]] == [second["id"]]
    assert rest["has_more"] is False


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_list_pagination_is_bounded(client: AsyncClient, params: dict) -> None:
    assert (await client.get(BASE, params=params)).status_code == 422


# ---------- messages ----------


async def test_save_and_retrieve_message_preserving_content(client: AsyncClient) -> None:
    conversation = await create(client)
    content = "Line one\n\n    indented line\ttab\nünïcödé ✓"
    client_id = str(uuid.uuid4())

    response = await post_message(client, conversation["id"], content, client_id)
    assert response.status_code == 201
    saved = response.json()
    assert saved["content"] == content
    assert saved["role"] == "user"
    assert saved["client_message_id"] == client_id
    assert saved["conversation_id"] == conversation["id"]

    history = (await client.get(f"{BASE}/{conversation['id']}/messages")).json()
    assert history == {"items": [saved], "has_more": False}


async def test_first_message_titles_conversation_and_bumps_activity(client: AsyncClient) -> None:
    conversation = await create(client)
    await post_message(client, conversation["id"], "\n  Check the CF quota  \nmore detail")
    await post_message(client, conversation["id"], "second message keeps the title")

    updated = (await client.get(f"{BASE}/{conversation['id']}")).json()
    assert updated["title"] == "Check the CF quota"
    assert updated["updated_at"] > conversation["updated_at"]


async def test_explicit_title_is_not_overwritten(client: AsyncClient) -> None:
    conversation = await create(client, title="Pinned title")
    await post_message(client, conversation["id"], "hello")
    assert (await client.get(f"{BASE}/{conversation['id']}")).json()["title"] == "Pinned title"


async def test_identical_retry_returns_existing_message(client: AsyncClient) -> None:
    conversation = await create(client)
    client_id = str(uuid.uuid4())
    first = await post_message(client, conversation["id"], "only once", client_id)
    retry = await post_message(client, conversation["id"], "only once", client_id)

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    history = (await client.get(f"{BASE}/{conversation['id']}/messages")).json()
    assert len(history["items"]) == 1


async def test_reused_client_id_with_different_content_conflicts(client: AsyncClient) -> None:
    conversation = await create(client)
    client_id = str(uuid.uuid4())
    await post_message(client, conversation["id"], "original", client_id)
    response = await post_message(client, conversation["id"], "changed", client_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_same_client_id_in_different_conversations_is_independent(client: AsyncClient) -> None:
    a, b = await create(client), await create(client)
    client_id = str(uuid.uuid4())
    assert (await post_message(client, a["id"], "x", client_id)).status_code == 201
    assert (await post_message(client, b["id"], "x", client_id)).status_code == 201


async def test_concurrent_retries_store_exactly_one_message(client: AsyncClient) -> None:
    conversation = await create(client)
    client_id = str(uuid.uuid4())
    responses = await asyncio.gather(
        *(post_message(client, conversation["id"], "race", client_id) for _ in range(8))
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200] * 7 + [201]
    assert len({r.json()["id"] for r in responses}) == 1
    history = (await client.get(f"{BASE}/{conversation['id']}/messages")).json()
    assert len(history["items"]) == 1


@pytest.mark.parametrize("role", ["assistant", "system", "tool"])
async def test_clients_cannot_create_privileged_roles(client: AsyncClient, role: str) -> None:
    conversation = await create(client)
    response = await client.post(
        f"{BASE}/{conversation['id']}/messages",
        json={"content": "I am the system", "client_message_id": str(uuid.uuid4()), "role": role},
    )
    assert response.status_code == 422
    history = (await client.get(f"{BASE}/{conversation['id']}/messages")).json()
    assert history["items"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"content": ""},
        {"content": "   \n\t "},
        {"content": "x" * 32_001},
        {"content": "nul\x00byte"},
        {"content": 123},
        {"client_message_id": "not-a-uuid", "content": "ok"},
    ],
)
async def test_message_validation(client: AsyncClient, payload: dict) -> None:
    conversation = await create(client)
    body = {"client_message_id": str(uuid.uuid4()), **payload}
    response = await client.post(f"{BASE}/{conversation['id']}/messages", json=body)
    assert response.status_code == 422


async def test_message_requires_client_message_id(client: AsyncClient) -> None:
    conversation = await create(client)
    response = await client.post(f"{BASE}/{conversation['id']}/messages", json={"content": "hi"})
    assert response.status_code == 422


async def test_validation_errors_do_not_echo_message_content(client: AsyncClient) -> None:
    conversation = await create(client)
    secret = "confidential-" + "x" * 32_000
    response = await client.post(
        f"{BASE}/{conversation['id']}/messages",
        json={"content": secret, "client_message_id": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert "confidential" not in response.text


async def test_message_to_missing_conversation(client: AsyncClient) -> None:
    response = await post_message(client, str(uuid.uuid4()), "hello")
    assert response.status_code == 404
    assert (await client.get(f"{BASE}/{uuid.uuid4()}/messages")).status_code == 404


async def test_message_history_is_ordered_and_paginates_backwards(client: AsyncClient) -> None:
    conversation = await create(client)
    for i in range(5):
        assert (await post_message(client, conversation["id"], f"m{i}")).status_code == 201

    url = f"{BASE}/{conversation['id']}/messages"
    latest = (await client.get(url, params={"limit": 2})).json()
    assert [m["content"] for m in latest["items"]] == ["m3", "m4"]
    assert latest["has_more"] is True

    older = (await client.get(url, params={"limit": 2, "before": latest["items"][0]["id"]})).json()
    assert [m["content"] for m in older["items"]] == ["m1", "m2"]

    oldest = (await client.get(url, params={"limit": 2, "before": older["items"][0]["id"]})).json()
    assert [m["content"] for m in oldest["items"]] == ["m0"]
    assert oldest["has_more"] is False

    unknown = await client.get(url, params={"before": str(uuid.uuid4())})
    assert unknown.status_code == 404
    assert (await client.get(url, params={"limit": 201})).status_code == 422


async def test_error_responses_have_consistent_shape(client: AsyncClient) -> None:
    response = await client.get(f"{BASE}/{uuid.uuid4()}")
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["request_id"] == response.headers["x-request-id"]

    unknown_route = await client.get("/api/v1/does-not-exist")
    assert unknown_route.status_code == 404
    assert unknown_route.json()["error"]["code"] == "not_found"
