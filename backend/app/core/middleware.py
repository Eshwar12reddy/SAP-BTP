"""Request context middleware: request ids, access logging and last-resort error handling."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import PayloadTooLargeError, error_body
from app.core.logging import client_ip_var, request_id_var

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "x-request-id"
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]
# Accept a caller's id only if it is short and harmless to echo into logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Pure ASGI middleware, so it also wraps errors that escape every exception handler."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        client = scope.get("client")
        ip_token = client_ip_var.set(client[0][:45] if client else None)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                headers.extend(SECURITY_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except PayloadTooLargeError as exc:
            status_code = exc.status_code
            if not response_started:
                body = json.dumps(error_body(exc.code, exc.message)).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": exc.status_code,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (REQUEST_ID_HEADER.encode(), request_id.encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
        except Exception:
            status_code = 500
            logger.exception(
                "Unhandled exception while processing request",
                extra={"event": "unhandled_exception", **_request_fields(scope)},
            )
            if not response_started:
                body = json.dumps(
                    error_body("internal_error", "An unexpected error occurred.")
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (REQUEST_ID_HEADER.encode(), request_id.encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            level = logging.ERROR if status_code >= 500 else logging.INFO
            logger.log(
                level,
                "request completed",
                extra={
                    "event": "http_request",
                    **_request_fields(scope),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
            client_ip_var.reset(ip_token)


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


def _request_fields(scope: Scope) -> dict[str, object]:
    return {"method": scope.get("method"), "route": _route_template(scope)}


def _route_template(scope: Scope) -> str:
    """The matched route as a template, e.g. /api/v1/conversations/{conversation_id}.

    Identifiers and query strings stay out of the logs; unmatched paths are not echoed.
    Built from the full path because a route inside an included router only knows its
    router-relative path.
    """
    # FastAPI routes set "route"; plain Starlette routes (e.g. /docs) only set "endpoint".
    if scope.get("route") is None and scope.get("endpoint") is None:
        return "<unmatched>"
    names = {str(value): name for name, value in (scope.get("path_params") or {}).items()}
    segments = scope.get("path", "").split("/")
    return "/".join(f"{{{names[s]}}}" if s in names else s for s in segments)


class UploadSizeLimitMiddleware:
    """Bounds request bodies under ``path_prefix`` while they stream in.

    The multipart parser spools uploads to temporary files before any endpoint code runs,
    so the limit has to apply here: a declared Content-Length over the limit is refused at
    once, and an undeclared or understated body is cut off as soon as it passes the limit.
    """

    def __init__(self, app: ASGIApp, *, path_prefix: str, max_body_bytes: int) -> None:
        self.app = app
        self.path_prefix = path_prefix
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in ("POST", "PUT", "PATCH")
            or not scope["path"].startswith(self.path_prefix)
        ):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_body_bytes):
            await _send_too_large(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise PayloadTooLargeError()
            return message

        await self.app(scope, limited_receive, send)


async def _send_too_large(send: Send) -> None:
    error = PayloadTooLargeError()
    body = json.dumps(error_body(error.code, error.message)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
