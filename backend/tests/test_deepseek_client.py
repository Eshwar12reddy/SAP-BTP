"""DeepSeek client tests. Every HTTP call goes to httpx.MockTransport; nothing is billed."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

import httpx
import pytest

from app.core.config import SettingsError, load_settings
from app.integrations.deepseek_client import (
    DeepSeekAuthenticationError,
    DeepSeekClient,
    DeepSeekError,
    DeepSeekInsufficientBalanceError,
    DeepSeekInvalidRequestError,
    DeepSeekInvalidResponseError,
    DeepSeekNotConfiguredError,
    DeepSeekRateLimitedError,
    DeepSeekTimeoutError,
    DeepSeekUnavailableError,
)
from app.main import create_app

API_KEY = "sk-test-not-a-real-key-0123456789"
PROMPT = "Private BTP question about subaccount acme-prod"
MESSAGES = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": PROMPT}]

Handler = Callable[[httpx.Request], object]


def make_settings(**overrides: object):
    values: dict[str, object] = {
        "_env_file": None,
        "APP_ENV": "test",
        # Never connected to: these tests need no database.
        "DATABASE_URL": "postgresql+psycopg://unused:unused@127.0.0.1:1/unused",
        "HEALTH_CHECK_TIMEOUT": 0.5,
        "JWT_SECRET_KEY": "deepseek-test-jwt-secret-0123456789-abcdef",
        "DEEPSEEK_API_KEY": API_KEY,
    }
    values.update(overrides)
    return load_settings(**values)


def make_client(
    handler: Handler,
    sleeps: list[float] | None = None,
    clock: Callable[[], float] = time.monotonic,
    **overrides: object,
) -> DeepSeekClient:
    async def fake_sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    return DeepSeekClient(
        make_settings(**overrides),
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        clock=clock,
        sleep=fake_sleep,
    )


def completion(
    content: object = "Hello there.",
    finish_reason: object = "stop",
    usage: object = None,
) -> dict:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "hidden chain of thought",
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage
        if usage is not None
        else {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def ok(body: object = None) -> httpx.Response:
    return httpx.Response(200, json=completion() if body is None else body)


class Calls:
    """Handler that replays ``responses`` in order (the last one repeats) and counts calls."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses[min(len(self.requests), len(self.responses)) - 1]
        if isinstance(response, Exception):
            raise response
        return response


# ---------- configuration ----------


def test_settings_defaults_and_secret_handling() -> None:
    settings = make_settings(DEEPSEEK_API_KEY="", DEEPSEEK_MODEL=" ")
    assert settings.DEEPSEEK_API_KEY is None
    assert settings.DEEPSEEK_MODEL == "deepseek-flash"
    assert settings.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert settings.DEEPSEEK_THINKING_ENABLED is False
    assert API_KEY not in repr(make_settings())


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.com",
        "https://evil.example.com",
        "https://api.deepseek.com.evil.example",
        "https://user:pw@api.deepseek.com",
        "https://api.deepseek.com:8443",
    ],
)
def test_base_url_must_be_the_official_https_host(url: str) -> None:
    with pytest.raises(SettingsError, match="DEEPSEEK_BASE_URL"):
        make_settings(DEEPSEEK_BASE_URL=url)


# ---------- success ----------


async def test_success_returns_answer_and_usage_but_not_reasoning() -> None:
    calls = Calls(ok())
    async with make_client(calls) as client:
        result = await client.chat(MESSAGES, temperature=0.2, max_tokens=100)

    assert result.content == "Hello there."
    assert result.model == "deepseek-flash"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (12, 3, 15)
    assert not result.truncated
    assert "hidden" not in repr(result)

    request = calls.requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(request.content) == {
        "model": "deepseek-flash",
        "messages": MESSAGES,
        "max_tokens": 100,
        "stream": False,
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
    }


async def test_thinking_mode_and_defaults_in_payload() -> None:
    calls = Calls(ok())
    async with make_client(calls, DEEPSEEK_THINKING_ENABLED=True) as client:
        await client.chat(MESSAGES)
    body = json.loads(calls.requests[0].content)
    assert body["thinking"] == {"type": "enabled"}
    assert body["max_tokens"] == 2048
    assert "temperature" not in body


async def test_truncated_and_missing_usage_are_reported_not_invented() -> None:
    body = completion(finish_reason="length")
    del body["usage"]
    async with make_client(Calls(ok(body))) as client:
        result = await client.chat(MESSAGES)
    assert result.truncated
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (
        None,
        None,
        None,
    )


# ---------- configuration and input errors (no HTTP call) ----------


async def test_missing_api_key_fails_only_when_used() -> None:
    calls = Calls(ok())
    async with make_client(calls, DEEPSEEK_API_KEY="") as client:
        assert not client.configured
        with pytest.raises(DeepSeekNotConfiguredError) as caught:
            await client.chat(MESSAGES)
    assert caught.value.code == "deepseek_not_configured"
    assert caught.value.status_code == 503
    assert calls.requests == []


@pytest.mark.parametrize(
    ("messages", "kwargs"),
    [
        ([], {}),
        ("hello", {}),
        ([{"role": "user", "content": "a"}] * 4, {}),
        ([{"role": "user", "content": "x" * 21}], {}),
        ([{"role": "tool", "content": "a"}], {}),
        ([{"role": "user", "content": "   "}], {}),
        ([{"role": "user", "content": 5}], {}),
        ([{"role": "user", "content": "a", "name": "x"}], {}),
        (["not a mapping"], {}),
        ([{"role": "system", "content": "a"}], {}),
        (MESSAGES[1:], {"max_tokens": 101}),
        (MESSAGES[1:], {"max_tokens": 0}),
        (MESSAGES[1:], {"max_tokens": True}),
        (MESSAGES[1:], {"temperature": 2.5}),
        (MESSAGES[1:], {"temperature": float("nan")}),
    ],
)
async def test_invalid_input_is_rejected_before_any_http_call(messages, kwargs) -> None:
    calls = Calls(ok())
    limits = {
        "DEEPSEEK_MAX_INPUT_MESSAGES": 3,
        "DEEPSEEK_MAX_INPUT_CHARS": 20,
        "DEEPSEEK_MAX_OUTPUT_TOKENS": 100,
    }
    async with make_client(calls, **limits) as client:
        with pytest.raises(DeepSeekInvalidRequestError) as caught:
            await client.chat(messages, **kwargs)
    assert caught.value.status_code == 400
    assert calls.requests == []


async def test_temperature_is_rejected_in_thinking_mode() -> None:
    async with make_client(Calls(ok()), DEEPSEEK_THINKING_ENABLED=True) as client:
        with pytest.raises(DeepSeekInvalidRequestError, match="thinking mode"):
            await client.chat(MESSAGES, temperature=0.5)


# ---------- timeouts and retries ----------


async def test_http_timeout_is_retried_then_reported() -> None:
    calls, sleeps = Calls(httpx.ReadTimeout("read timed out")), []
    async with make_client(calls, sleeps) as client:
        with pytest.raises(DeepSeekTimeoutError) as caught:
            await client.chat(MESSAGES)
    assert len(calls.requests) == 3  # 1 attempt + DEEPSEEK_MAX_RETRIES
    assert len(sleeps) == 2
    assert all(0 < delay <= 8 for delay in sleeps)
    assert caught.value.reason == "http_timeout"


async def test_transient_failures_are_retried_until_success() -> None:
    calls, sleeps = (
        Calls(
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "2"}),
            ok(),
        ),
        [],
    )
    async with make_client(calls, sleeps) as client:
        result = await client.chat(MESSAGES)
    assert result.content == "Hello there."
    assert len(calls.requests) == 3
    assert sleeps[1] == 2.0  # Retry-After is honoured


async def test_connection_error_is_retried() -> None:
    calls = Calls(httpx.ConnectError("refused"), ok())
    async with make_client(calls, []) as client:
        assert (await client.chat(MESSAGES)).content == "Hello there."
    assert len(calls.requests) == 2


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, DeepSeekAuthenticationError),
        (402, DeepSeekInsufficientBalanceError),
        (400, DeepSeekInvalidRequestError),
        (422, DeepSeekInvalidRequestError),
        (501, DeepSeekUnavailableError),
    ],
)
async def test_non_retryable_statuses_fail_after_one_attempt(status, error_type) -> None:
    calls = Calls(httpx.Response(status, json={"error": {"message": "upstream detail"}}))
    async with make_client(calls, []) as client:
        with pytest.raises(error_type) as caught:
            await client.chat(MESSAGES)
    assert len(calls.requests) == 1
    assert caught.value.upstream_status == status
    assert "upstream detail" not in caught.value.message


async def test_retry_after_beyond_the_deadline_gives_up_immediately() -> None:
    calls, sleeps = Calls(httpx.Response(429, headers={"Retry-After": "600"})), []
    async with make_client(calls, sleeps) as client:
        with pytest.raises(DeepSeekRateLimitedError) as caught:
            await client.chat(MESSAGES)
    assert len(calls.requests) == 1
    assert sleeps == []
    assert caught.value.headers == {"Retry-After": "600"}


async def test_overall_deadline_bounds_a_hanging_request() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return ok()

    async with make_client(hang, DEEPSEEK_REQUEST_DEADLINE=0.05) as client:
        started = time.monotonic()
        with pytest.raises(DeepSeekTimeoutError) as caught:
            await client.chat(MESSAGES)
    assert time.monotonic() - started < 1
    assert caught.value.reason == "deadline"
    assert caught.value.status_code == 504


# ---------- rate and concurrency limits ----------


async def test_rate_limit_counts_retries_and_recovers_after_the_window() -> None:
    now = [1000.0]
    calls = Calls(httpx.Response(503))
    client = make_client(calls, [], clock=lambda: now[0], DEEPSEEK_REQUESTS_PER_MINUTE=2)
    async with client:
        with pytest.raises(DeepSeekRateLimitedError) as caught:
            await client.chat(MESSAGES)
        # Attempt 3 (the second retry) was refused locally instead of being sent.
        assert len(calls.requests) == 2
        assert caught.value.reason == "local_limit"
        assert caught.value.headers == {"Retry-After": "60"}

        calls.responses = (ok(),)
        with pytest.raises(DeepSeekRateLimitedError):
            await client.chat(MESSAGES)
        now[0] += 61
        assert (await client.chat(MESSAGES)).content == "Hello there."


async def test_concurrency_is_limited_and_permits_are_released() -> None:
    active = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ok()

    async with make_client(slow, DEEPSEEK_MAX_CONCURRENT_REQUESTS=2) as client:
        results = await asyncio.gather(*(client.chat(MESSAGES) for _ in range(6)))
    assert peak == 2
    assert len(results) == 6


async def test_cancellation_propagates_and_releases_the_permit() -> None:
    started = asyncio.Event()

    async def hang_first(request: httpx.Request) -> httpx.Response:
        if not started.is_set():
            started.set()
            await asyncio.sleep(5)
        return ok()

    async with make_client(hang_first, DEEPSEEK_MAX_CONCURRENT_REQUESTS=1) as client:
        task = asyncio.create_task(client.chat(MESSAGES))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The only permit was released, so the next call is not blocked.
        result = await asyncio.wait_for(client.chat(MESSAGES), timeout=1)
    assert result.content == "Hello there."


# ---------- invalid responses ----------


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, text="<html>gateway</html>"), "body_not_json"),
        (ok([]), "body_not_object"),
        (ok({"model": "deepseek-flash", "choices": []}), "missing_message"),
        (ok({**completion(), "model": None}), "missing_model"),
        (ok(completion(content=42)), "content_not_text"),
        (ok(completion(finish_reason=None)), "missing_finish_reason"),
        (ok(completion(finish_reason="tool_calls")), "unexpected_finish_reason"),
        (ok(completion(content="  ")), "empty_completion"),
        (ok(completion(content=None, finish_reason="length")), "empty_completion"),
        (ok(completion(usage={"prompt_tokens": "12"})), "usage_not_integer"),
        (ok(completion(usage=[1])), "usage_not_object"),
    ],
)
async def test_malformed_responses_are_rejected(response, reason) -> None:
    calls = Calls(response)
    async with make_client(calls, []) as client:
        with pytest.raises(DeepSeekInvalidResponseError) as caught:
            await client.chat(MESSAGES)
    assert caught.value.reason == reason
    assert caught.value.status_code == 502
    assert len(calls.requests) == 1


async def test_keep_alive_padding_and_content_filter_are_accepted() -> None:
    padded = httpx.Response(200, text="\n\n" + json.dumps(completion("", "content_filter")))
    async with make_client(Calls(padded)) as client:
        result = await client.chat(MESSAGES)
    assert (result.content, result.finish_reason) == ("", "content_filter")


async def test_server_side_abort_is_unavailable_and_not_retried() -> None:
    calls = Calls(ok(completion(finish_reason="insufficient_system_resource")))
    async with make_client(calls, []) as client:
        with pytest.raises(DeepSeekUnavailableError):
            await client.chat(MESSAGES)
    assert len(calls.requests) == 1


# ---------- secret safety ----------


async def test_errors_and_logs_never_contain_secrets_or_prompts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.integrations.deepseek_client")
    calls = Calls(
        httpx.ConnectError(f"failed with Authorization: Bearer {API_KEY}"),
        httpx.Response(401, json={"error": f"bad key {API_KEY} for prompt {PROMPT}"}),
    )
    async with make_client(calls, []) as client:
        with pytest.raises(DeepSeekError) as caught:
            await client.chat(MESSAGES)

    error = caught.value
    assert error.code == "deepseek_authentication_failed"
    # No httpx exception (whose text could hold upstream detail) is chained to the error.
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    for text in (str(error), error.message, repr(error.__dict__)):
        assert API_KEY not in text and PROMPT not in text
    assert caplog.records, "retry and failure events are logged"
    for record in caplog.records:
        dumped = repr(record.__dict__)
        assert API_KEY not in dumped and PROMPT not in dumped
    events = [getattr(record, "event", None) for record in caplog.records]
    assert events == ["deepseek_request_started", "deepseek_retry", "deepseek_request_failed"]


# ---------- lifecycle ----------


async def test_closed_client_refuses_requests() -> None:
    client = make_client(Calls(ok()))
    await client.close()
    assert client._http.is_closed  # type: ignore[attr-defined]
    with pytest.raises(DeepSeekUnavailableError):
        await client.chat(MESSAGES)


async def test_lifespan_creates_one_shared_client_and_closes_it() -> None:
    app = create_app(make_settings(DEEPSEEK_API_KEY=""))
    async with app.router.lifespan_context(app):
        client = app.state.deepseek_client
        assert isinstance(client, DeepSeekClient)
        assert not client.configured  # the app starts without a key
        assert not client._http.is_closed  # type: ignore[attr-defined]
    assert client._http.is_closed  # type: ignore[attr-defined]
