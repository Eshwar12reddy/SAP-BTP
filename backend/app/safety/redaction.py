"""Masks credentials before text reaches the model, tool results or logs.

Two layers: the exact values of secrets this application is configured with, and patterns
for common credential formats. Neither is complete, so it is the last line of defence:
authorization and data minimization decide what is sent in the first place.

Only secrets of at least ``MIN_KNOWN_SECRET_LENGTH`` characters are matched by value, and
results report a count, never which secret matched. A redaction therefore cannot serve as an
oracle for guessing a configured secret.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.core.config import Settings

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
MAX_SCAN_CHARS = 200_000
MIN_KNOWN_SECRET_LENGTH = 12

_SECRET_NAME = (
    r"(?:password|passwd|pwd|secret|token|client[_-]?secret|api[_-]?key|access[_-]?key|"
    r"secret[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|"
    r"private[_-]?key|jwt[_-]?secret(?:[_-]?key)?)"
)
# (pattern, replacement). Group 1, where present, is kept so the text stays readable.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (
        re.compile(
            r"(\b(?:proxy-)?authorization\b[\"']?\s*[:=]\s*[\"']?)"
            r"(?:(?:bearer|basic|token|digest)\s+)?[^\s\"',;]+",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"(\bbearer\s+)[A-Za-z0-9\-._~+/]{12,}=*", re.IGNORECASE), rf"\1{REDACTED}"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),
    # scheme://user:password@host keeps the scheme, user and host.
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)", re.IGNORECASE), rf"\1{REDACTED}\2"),
    (
        # Also prefixed names such as DEEPSEEK_API_KEY= or db-password:.
        re.compile(
            rf"(\b(?:[a-z0-9]+[_-])*{_SECRET_NAME}\b[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}}&]{{6,}}",
            re.IGNORECASE,
        ),
        rf"\1{REDACTED}",
    ),
)
# Structured data: values under these keys are always masked.
_SENSITIVE_KEY = re.compile(
    rf"^(?:.*[_-])?(?:{_SECRET_NAME}|token|credentials?|authorization|cookie|"
    r"connection[_-]?string)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Redaction:
    text: str
    count: int


class Redactor:
    def __init__(self, known_secrets: Iterable[str | None] = ()) -> None:
        values = sorted(
            {s for s in known_secrets if s and len(s) >= MIN_KNOWN_SECRET_LENGTH},
            key=len,
            reverse=True,
        )
        self._known = re.compile("|".join(re.escape(v) for v in values)) if values else None

    @classmethod
    def from_settings(cls, settings: Settings) -> Redactor:
        secrets: list[str | None] = []
        for value in (
            settings.DEEPSEEK_API_KEY,
            settings.OPENAI_API_KEY,
            settings.JWT_SECRET_KEY,
            settings.POSTGRES_PASSWORD,
        ):
            secrets.append(value.get_secret_value() if value is not None else None)
        password = settings.database_url.password
        if password:
            secrets += [password, quote(password, safe="")]
        return cls(secrets)

    def redact(self, text: str) -> Redaction:
        count = 0
        if len(text) > MAX_SCAN_CHARS:
            # Bounded cost; the unscanned remainder is dropped, never passed on.
            text = f"{text[:MAX_SCAN_CHARS]}\n{TRUNCATED}"
            count += 1
        if self._known is not None:
            text, n = self._known.subn(REDACTED, text)
            count += n
        for pattern, replacement in _PATTERNS:
            text, n = pattern.subn(replacement, text)
            count += n
        return Redaction(text, count)

    def redact_text(self, text: str) -> str:
        return self.redact(text).text

    def redact_data(self, value: Any) -> tuple[Any, int]:
        """Mask sensitive keys and secret-looking strings in JSON-like data."""
        if isinstance(value, str):
            result = self.redact(value)
            return result.text, result.count
        if isinstance(value, dict):
            total, masked = 0, {}
            for key, item in value.items():
                if isinstance(key, str) and _SENSITIVE_KEY.match(key) and item not in (None, ""):
                    masked[key] = REDACTED
                    total += 1
                else:
                    masked[key], n = self.redact_data(item)
                    total += n
            return masked, total
        if isinstance(value, list):
            total, items = 0, []
            for item in value:
                masked_item, n = self.redact_data(item)
                items.append(masked_item)
                total += n
            return items, total
        return value, 0
