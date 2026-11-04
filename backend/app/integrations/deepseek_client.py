"""Async client for DeepSeek's chat-completions API (https://api-docs.deepseek.com).

Create one instance per process (the FastAPI lifespan does) and share it: it owns the
connection pool, the concurrency limit and the requests-per-minute window. The client does
not authorize anything; callers must only put data into a prompt that the user may read.

Limits are in-process. Each worker process has its own budget, so the account-wide rate is
DEEPSEEK_REQUESTS_PER_MINUTE multiplied by the number of workers.

Retries can bill twice: after a timeout or a dropped connection it is unknown whether
DeepSeek already processed (and charged for) the request, and a retry sends it again.
DEEPSEEK_MAX_RETRIES bounds that cost; set it to 0 where a repeat is unacceptable.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx

from app.core.config import Settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
ALLOWED_ROLES = frozenset({"system", "user", "assistant"})

_POOL_TIMEOUT = 5.0
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 8.0
_RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})
_RETURNED_FINISH_REASONS = frozenset({"stop", "length", "content_filter"})
# The model stopped early because of the server, not because of the request.
_UNAVAILABLE_FINISH_REASONS = frozenset({"insufficient_system_resource", "aborted"})
_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


class DeepSeekError(AppError):
    """Base for DeepSeek failures. Messages are fixed text that is safe to return to clients.

    ``reason`` and ``upstream_status`` are internal diagnostics for logs; they are not part
    of the API error response.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        reason: str | None = None,
        upstream_status: int | None = None,
        retry_after: float | None = None,
        retryable: bool = False,
    ) -> None:
        headers = None
        if retry_after is not None:
            headers = {"Retry-After": str(max(1, math.ceil(retry_after)))}
        super().__init__(message, headers=headers)
        self.reason = reason
        self.upstream_status = upstream_status
        self.retry_after = retry_after
        self.retryable = retryable


class DeepSeekNotConfiguredError(DeepSeekError):
    status_code = 503
    code = "deepseek_not_configured"
    message = "The AI service is not configured."


class DeepSeekInvalidRequestError(DeepSeekError):
    status_code = 400
    code = "deepseek_invalid_request"
    message = "The AI service rejected the request."


class DeepSeekAuthenticationError(DeepSeekError):
    status_code = 503
    code = "deepseek_authentication_failed"
    message = "The AI service is unavailable: its credentials were rejected."


class DeepSeekInsufficientBalanceError(DeepSeekError):
    status_code = 503
    code = "deepseek_insufficient_balance"
    message = "The AI service is unavailable: the account balance is insufficient."


class DeepSeekRateLimitedError(DeepSeekError):
    status_code = 429
    code = "deepseek_rate_limited"
    message = "The AI service is busy. Please wait and try again."


class DeepSeekTimeoutError(DeepSeekError):
    status_code = 504
    code = "deepseek_timeout"
    message = "The AI service did not respond in time."


class DeepSeekUnavailableError(DeepSeekError):
    status_code = 503
    code = "deepseek_unavailable"
    message = "The AI service is temporarily unavailable. Please try again shortly."


class DeepSeekInvalidResponseError(DeepSeekError):
    status_code = 502
    code = "deepseek_invalid_response"
    message = "The AI service returned an invalid response."


@dataclass(frozen=True, slots=True)
class DeepSeekResult:
    """The visible answer. Thinking-mode ``reasoning_content`` is never included.

    Token counts are None when DeepSeek did not report them.
    """

    content: str
    model: str
    finish_reason: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

    @property
    def truncated(self) -> bool:
        """The answer reached max_tokens and is incomplete."""
        return self.finish_reason == "length"


class _RequestWindow:
    """Rolling 60-second window of HTTP attempts, retries included.

    Checking and recording happen with no ``await`` in between, so concurrent tasks on the
    event loop cannot both take the last slot, and no lock is held during network calls.
    """

    def __init__(self, limit: int, clock: Callable[[], float], window: float = 60.0) -> None:
        self._limit = limit
        self._clock = clock
        self._window = window
        self._attempts: deque[float] = deque()

    def try_acquire(self) -> float | None:
        """Record an attempt and return None, or return the seconds until a slot frees up."""
        now = self._clock()
        while self._attempts and now - self._attempts[0] >= self._window:
            self._attempts.popleft()
        if len(self._attempts) >= self._limit:
            return self._window - (now - self._attempts[0])
        self._attempts.append(now)
        return None


class DeepSeekClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(settings.DEEPSEEK_MAX_CONCURRENT_REQUESTS)
        self._window = _RequestWindow(settings.DEEPSEEK_REQUESTS_PER_MINUTE, clock)
        connections = settings.DEEPSEEK_MAX_CONCURRENT_REQUESTS
        self._http = httpx.AsyncClient(
            base_url=settings.DEEPSEEK_BASE_URL,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(
                connect=settings.DEEPSEEK_CONNECT_TIMEOUT,
                read=settings.DEEPSEEK_READ_TIMEOUT,
                write=settings.DEEPSEEK_WRITE_TIMEOUT,
                pool=_POOL_TIMEOUT,
            ),
            limits=httpx.Limits(
                max_connections=connections, max_keepalive_connections=connections
            ),
            follow_redirects=False,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return self._settings.DEEPSEEK_API_KEY is not None

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def chat(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> DeepSeekResult:
        """Return DeepSeek's answer to ``messages`` (each ``{"role", "content"}``).

        Every failure raises a ``DeepSeekError`` subclass. The whole call, including
        queueing, retries and backoff, ends within DEEPSEEK_REQUEST_DEADLINE seconds.
        """
        api_key = self._settings.DEEPSEEK_API_KEY
        if api_key is None:
            raise DeepSeekNotConfiguredError()
        if self._http.is_closed:
            raise DeepSeekUnavailableError(reason="client_closed")
        payload = self._build_payload(messages, temperature, max_tokens)
        headers = {"Authorization": f"Bearer {api_key.get_secret_value()}"}

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self._settings.DEEPSEEK_REQUEST_DEADLINE
        attempt = 0
        logger.debug(
            "DeepSeek request started",
            extra={"event": "deepseek_request_started", "model": self._settings.DEEPSEEK_MODEL},
        )
        try:
            async with asyncio.timeout_at(deadline), self._semaphore:
                while True:
                    attempt += 1
                    try:
                        result = await self._send(payload, headers)
                        break
                    except DeepSeekError as error:
                        delay = self._retry_delay(error, attempt, deadline - loop.time())
                        if delay is None:
                            raise
                        self._log(
                            "Retrying DeepSeek request",
                            "deepseek_retry",
                            error,
                            attempt,
                            started,
                            retry_after=round(delay, 2),
                        )
                        await self._sleep(delay)
        except TimeoutError:
            error = DeepSeekTimeoutError(reason="deadline")
            self._log("DeepSeek request failed", "deepseek_request_failed", error, attempt, started)
            raise error from None
        except DeepSeekError as error:
            self._log("DeepSeek request failed", "deepseek_request_failed", error, attempt, started)
            raise

        logger.info(
            "DeepSeek request completed",
            extra={
                "event": "deepseek_request_completed",
                "model": result.model,
                "attempt": attempt,
                "finish_reason": result.finish_reason,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "duration_ms": _elapsed_ms(started),
            },
        )
        return result

    async def _send(self, payload: dict[str, Any], headers: dict[str, str]) -> DeepSeekResult:
        """One HTTP attempt. Upstream bodies and httpx messages are never propagated."""
        wait = self._window.try_acquire()
        if wait is not None:
            logger.warning(
                "DeepSeek request refused by the local rate limit",
                extra={"event": "deepseek_request_rate_limited", "retry_after": round(wait, 2)},
            )
            raise DeepSeekRateLimitedError(reason="local_limit", retry_after=wait)
        try:
            response = await self._http.post(CHAT_COMPLETIONS_PATH, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise DeepSeekTimeoutError(reason="http_timeout", retryable=True) from None
        except httpx.RequestError:
            raise DeepSeekUnavailableError(reason="connection_error", retryable=True) from None
        return _parse_response(response)

    def _retry_delay(self, error: DeepSeekError, attempt: int, remaining: float) -> float | None:
        """Seconds to wait before the next attempt, or None to give up now."""
        if not error.retryable or attempt > self._settings.DEEPSEEK_MAX_RETRIES:
            return None
        if error.retry_after is not None:
            delay = error.retry_after
        else:
            backoff = min(_BACKOFF_CAP, _BACKOFF_BASE * 2 ** (attempt - 1))
            delay = backoff / 2 + random.uniform(0, backoff / 2)
        # Waiting past the deadline would only turn this error into a timeout.
        return delay if delay < remaining else None

    def _build_payload(
        self,
        messages: Sequence[Mapping[str, str]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        settings = self._settings
        if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence) or not messages:
            raise _bad_request("messages must be a non-empty list.")
        if len(messages) > settings.DEEPSEEK_MAX_INPUT_MESSAGES:
            raise _bad_request(
                f"Too many messages: the limit is {settings.DEEPSEEK_MAX_INPUT_MESSAGES}."
            )
        cleaned = [_validate_message(message, index) for index, message in enumerate(messages)]
        if not any(message["role"] == "user" for message in cleaned):
            raise _bad_request("At least one user message is required.")
        if sum(len(message["content"]) for message in cleaned) > settings.DEEPSEEK_MAX_INPUT_CHARS:
            raise _bad_request(
                f"The input is too long: the limit is {settings.DEEPSEEK_MAX_INPUT_CHARS} "
                "characters."
            )

        thinking = settings.DEEPSEEK_THINKING_ENABLED
        payload: dict[str, Any] = {
            "model": settings.DEEPSEEK_MODEL,
            "messages": cleaned,
            "max_tokens": _validate_max_tokens(max_tokens, settings.DEEPSEEK_MAX_OUTPUT_TOKENS),
            "stream": False,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        if temperature is not None:
            if thinking:
                raise _bad_request("temperature is not supported in thinking mode.")
            payload["temperature"] = _validate_temperature(temperature)
        return payload

    def _log(
        self,
        message: str,
        event: str,
        error: DeepSeekError,
        attempt: int,
        started: float,
        **fields: object,
    ) -> None:
        logger.warning(
            message,
            extra={
                "event": event,
                "model": self._settings.DEEPSEEK_MODEL,
                "error_code": error.code,
                "reason": error.reason,
                "upstream_status": error.upstream_status,
                "attempt": attempt,
                "duration_ms": _elapsed_ms(started),
                **fields,
            },
        )


def _validate_message(message: object, index: int) -> dict[str, str]:
    # Errors name the position only; message text is never echoed.
    if not isinstance(message, Mapping) or set(message) != {"role", "content"}:
        raise _bad_request(f"Message {index} must have exactly 'role' and 'content'.")
    role, content = message["role"], message["content"]
    if not isinstance(role, str) or role not in ALLOWED_ROLES:
        raise _bad_request(f"Message {index} has an unsupported role.")
    if not isinstance(content, str) or not content.strip():
        raise _bad_request(f"Message {index} must have non-empty text content.")
    return {"role": role, "content": content}


def _validate_max_tokens(value: object, limit: int) -> int:
    if value is None:
        return limit
    if type(value) is not int or not 1 <= value <= limit:
        raise _bad_request(f"max_tokens must be an integer from 1 to {limit}.")
    return value


def _validate_temperature(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 2:
        raise _bad_request("temperature must be a number from 0 to 2.")
    return float(value)


def _parse_response(response: httpx.Response) -> DeepSeekResult:
    status = response.status_code
    if status == 200:
        return _parse_completion(response)
    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
    if status in (401, 403):
        raise DeepSeekAuthenticationError(upstream_status=status)
    if status == 402:
        raise DeepSeekInsufficientBalanceError(upstream_status=status)
    if status == 429:
        raise DeepSeekRateLimitedError(
            reason="upstream_limit", upstream_status=status, retry_after=retry_after,
            retryable=True,
        )
    if 400 <= status < 500:
        raise DeepSeekInvalidRequestError(reason="upstream_rejected", upstream_status=status)
    raise DeepSeekUnavailableError(
        upstream_status=status,
        retry_after=retry_after,
        retryable=status in _RETRYABLE_SERVER_STATUSES,
    )


def _parse_completion(response: httpx.Response) -> DeepSeekResult:
    # Leading blank lines are DeepSeek's keep-alive padding; JSON parsing skips them.
    try:
        body = response.json()
    except ValueError:
        raise _invalid("body_not_json") from None
    if not isinstance(body, dict):
        raise _invalid("body_not_object")
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        raise _invalid("missing_message")
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise _invalid("missing_model")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise _invalid("content_not_text")
    content = content or ""
    finish_reason = choice.get("finish_reason")  # type: ignore[union-attr]
    if not isinstance(finish_reason, str):
        raise _invalid("missing_finish_reason")
    if finish_reason in _UNAVAILABLE_FINISH_REASONS:
        raise DeepSeekUnavailableError(reason=f"finish_{finish_reason}", upstream_status=200)
    if finish_reason not in _RETURNED_FINISH_REASONS:
        raise _invalid("unexpected_finish_reason")
    if not content.strip() and finish_reason != "content_filter":
        raise _invalid("empty_completion")
    return DeepSeekResult(
        content=content, model=model, finish_reason=finish_reason, **_parse_usage(body)
    )


def _parse_usage(body: dict[str, Any]) -> dict[str, int | None]:
    usage = body.get("usage")
    if usage is None:
        return dict.fromkeys(_USAGE_FIELDS)
    if not isinstance(usage, dict):
        raise _invalid("usage_not_object")
    counts = {name: usage.get(name) for name in _USAGE_FIELDS}
    if any(n is not None and (type(n) is not int or n < 0) for n in counts.values()):
        raise _invalid("usage_not_integer")
    return counts


def _retry_after_seconds(value: str | None) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date); None if unusable."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            return None
        seconds = (when - datetime.now(UTC)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0.0)


def _bad_request(message: str) -> DeepSeekInvalidRequestError:
    return DeepSeekInvalidRequestError(message, reason="validation")


def _invalid(reason: str) -> DeepSeekInvalidResponseError:
    return DeepSeekInvalidResponseError(reason=reason, upstream_status=200)


def _elapsed_ms(started: float) -> float:
    return round((asyncio.get_running_loop().time() - started) * 1000, 1)
