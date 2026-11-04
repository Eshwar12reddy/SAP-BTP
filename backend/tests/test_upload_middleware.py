"""The streamed size limit must become HTTP 413 before response headers start."""

from __future__ import annotations

import json

import pytest

from app.core.middleware import RequestContextMiddleware, UploadSizeLimitMiddleware


@pytest.mark.parametrize("declared", [True, False])
async def test_oversized_request_returns_one_413_response(declared: bool) -> None:
    async def endpoint(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestContextMiddleware(
        UploadSizeLimitMiddleware(endpoint, path_prefix="/api/v1/knowledge", max_body_bytes=4)
    )
    headers = [(b"content-length", b"8")] if declared else []
    scope = {"type": "http", "method": "POST", "path": "/api/v1/knowledge/sources", "headers": headers}
    messages = iter(
        [
            {"type": "http.request", "body": b"12345678", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    starts = [message for message in sent if message["type"] == "http.response.start"]
    bodies = [message for message in sent if message["type"] == "http.response.body"]
    assert len(starts) == 1 and starts[0]["status"] == 413
    assert len(bodies) == 1
    assert json.loads(bodies[0]["body"])["error"]["code"] == "payload_too_large"
