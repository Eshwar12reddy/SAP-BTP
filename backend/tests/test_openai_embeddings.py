"""OpenAI embeddings are exercised with an in-memory HTTP transport only."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.openai_embeddings import (
    EmbeddingsDisabled,
    EmbeddingsError,
    EmbeddingsNotConfigured,
    OpenAIEmbeddingsClient,
)


def _body(payload: dict) -> dict:
    return {
        "model": payload["model"],
        "data": [
            {"index": i, "embedding": [float(i + 1)] * payload["dimensions"]}
            for i, _ in enumerate(payload["input"])
        ],
    }


@pytest.mark.asyncio
async def test_batches_and_checks_dimensions(settings):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json=_body(payload))

    configured = settings.model_copy(update={
        "OPENAI_API_KEY": SecretStr("sk-test-key-not-real-123456"),
        "OPENAI_EMBEDDINGS_ENABLED": True,
        "OPENAI_EMBEDDING_BATCH_SIZE": 2,
    })
    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(handler))
    try:
        result = await client.embed_texts(["one", "two", "three"])
    finally:
        await client.close()
    assert [len(call["input"]) for call in requests] == [2, 1]
    assert all(call["model"] == "text-embedding-3-small" for call in requests)
    assert all(len(vector) == 1536 for vector in result)


@pytest.mark.asyncio
async def test_retry_and_api_failure_are_sanitized(settings):
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": {"message": "secret-bearing response"}})
        return httpx.Response(200, json=_body(__import__("json").loads(request.content)))

    configured = settings.model_copy(update={
        "OPENAI_API_KEY": SecretStr("sk-test-key-not-real-123456"),
        "OPENAI_EMBEDDINGS_ENABLED": True,
        "OPENAI_EMBEDDING_MAX_RETRIES": 1,
    })
    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(handler))
    try:
        assert len(await client.embed_texts(["text"])) == 1
    finally:
        await client.close()
    assert calls == 2

    async def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "do not expose this"}})

    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(EmbeddingsError, match="rejected") as error:
            await client.embed_texts(["text"])
    finally:
        await client.close()
    assert "expose" not in str(error.value)


@pytest.mark.asyncio
async def test_disabled_and_missing_key_never_call_transport(settings):
    async def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("unexpected network call")

    client = OpenAIEmbeddingsClient(settings, transport=httpx.MockTransport(unexpected))
    try:
        with pytest.raises(EmbeddingsDisabled):
            await client.embed_texts(["text"])
    finally:
        await client.close()

    configured = settings.model_copy(update={"OPENAI_EMBEDDINGS_ENABLED": True})
    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(unexpected))
    try:
        with pytest.raises(EmbeddingsNotConfigured):
            await client.embed_texts(["text"])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_wrong_model_and_bad_dimensions_fail_closed(settings):
    configured = settings.model_copy(update={
        "OPENAI_API_KEY": SecretStr("sk-test-key-not-real-123456"),
        "OPENAI_EMBEDDINGS_ENABLED": True,
    })

    async def wrong_model(request: httpx.Request) -> httpx.Response:
        body = _body(__import__("json").loads(request.content))
        body["model"] = "other-model"
        return httpx.Response(200, json=body)

    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(wrong_model))
    try:
        with pytest.raises(EmbeddingsError, match="model_mismatch"):
            await client.embed_texts(["text"])
    finally:
        await client.close()

    async def wrong_dimensions(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        body = _body(payload)
        body["data"][0]["embedding"] = [0.1]
        return httpx.Response(200, json=body)

    client = OpenAIEmbeddingsClient(configured, transport=httpx.MockTransport(wrong_dimensions))
    try:
        with pytest.raises(EmbeddingsError, match="dimensions_mismatch"):
            await client.embed_texts(["text"])
    finally:
        await client.close()
