"""Structured JSON logging to standard output.

Every record carries the current request id (when inside a request) and any
structured fields passed through ``extra=``. Request bodies, message contents,
headers and connection strings are never logged.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)

# Fields copied from ``extra=`` into the JSON document, in output order.
_STRUCTURED_FIELDS = (
    "event",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "error_type",
    "error_code",
    "app_env",
    "database",
    "database_host",
    "database_port",
    "model",
    "configured",
    "attempt",
    "upstream_status",
    "reason",
    "retry_after",
    "finish_reason",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "route",
    "outcome",
    "llm_calls",
    "tool_calls",
    "trust_level",
    "source",
    "signals",
    "redactions",
    "tool",
)


class JsonFormatter(logging.Formatter):
    def __init__(self, redact: Callable[[str], str] | None = None) -> None:
        super().__init__()
        # Masks secrets in free text (messages, tracebacks); structured fields are metadata.
        self._redact = redact or (lambda text: text)

    def format(self, record: logging.LogRecord) -> str:
        document: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "message": self._redact(record.getMessage()),
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                document[field] = value
        if record.exc_info:
            document["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(document, default=str, ensure_ascii=False)


def configure_logging(level: str, redact: Callable[[str], str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(redact))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    # Route uvicorn through the JSON handler. Its access log is replaced by the
    # request middleware, which logs the route template instead of the raw URL.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True

    # These log full SQL statements / request URLs at INFO; keep them quiet.
    for name in ("sqlalchemy", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
