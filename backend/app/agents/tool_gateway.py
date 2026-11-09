"""Typed gateway between orchestration and the frozen tool registry."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.contracts import AgentContext
from app.auth.authorization import load_active_grants
from app.tools.contracts import ToolResult, ToolStatus
from app.tools.registry import ToolRegistry


class ToolCallRequest(BaseModel):
    """A bounded tool proposal; it does not authorize or select an implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    arguments: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("arguments")
    @classmethod
    def _bounded_arguments(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            raise ValueError("tool arguments must be JSON-compatible") from None
        if len(encoded) > 16_000:
            raise ValueError("tool arguments are too large")
        return value


class ToolCallResult(ToolResult):
    """Tool output exposed to the orchestrator after registry validation and redaction."""


class ToolGateway:
    """Single execution boundary with a per-run call budget and output bound."""

    def __init__(self, registry: ToolRegistry, *, max_calls: int = 2) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be positive")
        self._registry = registry
        self._max_calls = max_calls
        self._calls = 0

    async def execute(
        self, request: ToolCallRequest, context: AgentContext
    ) -> ToolCallResult:
        if context.session is not None:
            # A graph may resume after a grant changed. Refresh the principal immediately
            # before the registry and tool-level scope checks, not just at HTTP entry.
            context.principal.grants = await load_active_grants(
                context.session, context.principal.user_id
            )
            context.principal._coverage.clear()
        if self._calls >= self._max_calls:
            raise RuntimeError("agent tool-call budget exceeded")
        self._calls += 1
        result = await self._registry.execute(request.name, request.arguments, context)
        if result.data is not None:
            encoded = json.dumps(result.data, separators=(",", ":"), ensure_ascii=False)
            if len(encoded) > 64_000:
                return ToolCallResult(
                    tool_name=result.tool_name,
                    status=ToolStatus.FAILED,
                    source_type=result.source_type,
                    error_code="tool_result_too_large",
                )
        return ToolCallResult.model_validate(result.model_dump())
