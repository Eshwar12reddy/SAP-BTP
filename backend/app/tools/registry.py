"""The single registry through which every agent tool runs.

``execute`` is the only execution path. In order it checks: the tool is registered, it is
read-only (write tools are refused in this phase), the caller holds the tool's permission,
and the arguments match the input model. The tool itself then authorizes the specific BTP
resource against the caller's scope. Registration never implies authorization.

A tool name is only ever a dictionary key here, never an import path or callable, so text
(from a user, a document or the model) cannot reach a function that was not registered at
startup. The registry is frozen once the application has started. Tool output is redacted
before it is returned; it is data, never instructions.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.safety.redaction import Redactor
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome
from app.tools.contracts import (
    OperationType,
    SourceType,
    Tool,
    ToolDenied,
    ToolResult,
    ToolStatus,
    ToolUnavailable,
)

if TYPE_CHECKING:
    from app.agents.contracts import AgentContext

logger = logging.getLogger(__name__)

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class ToolRegistry:
    def __init__(self, redactor: Redactor | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._frozen = False
        self._redactor = redactor or Redactor()

    def register(self, tool: Tool) -> None:
        if self._frozen:
            raise RuntimeError("the tool registry is frozen")
        if not _TOOL_NAME.match(tool.name):
            raise ValueError(f"invalid tool name: {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def freeze(self) -> None:
        """No further registrations (called once startup has registered every tool)."""
        self._frozen = True

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name) if isinstance(name, str) else None

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    async def execute(
        self, name: str, arguments: Mapping[str, Any], context: AgentContext
    ) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            # Nothing ran, so there is no source to report. Unknown names are not echoed.
            shown = name if isinstance(name, str) and _TOOL_NAME.match(name) else "invalid"
            _log_denial(shown, "tool_not_registered")
            return _result(shown, ToolStatus.DENIED, None, "tool_not_registered")

        def outcome(status: ToolStatus, code: str) -> ToolResult:
            return _result(tool.name, status, tool.source_type, code)

        if tool.operation_type is not OperationType.READ:
            _log_denial(tool.name, "write_tools_disabled")
            return outcome(ToolStatus.DENIED, "write_tools_disabled")
        if not context.principal.has_permission(tool.required_permission):
            _log_denial(tool.name, "missing_permission")
            await _audit_denial(context, tool, "missing_permission")
            return outcome(ToolStatus.DENIED, "missing_permission")
        try:
            arguments = tool.input_model.model_validate(dict(arguments))
        except (ValidationError, TypeError, ValueError):
            # Validation messages can echo input values; only the code is reported.
            return outcome(ToolStatus.INVALID_INPUT, "invalid_tool_input")

        try:
            async with asyncio.timeout(tool.timeout_seconds):
                output = await tool.run(arguments, context)
            data = tool.output_model.model_validate(output).model_dump(mode="json")
        except ToolDenied as exc:
            _log_denial(tool.name, exc.code)
            await _audit_denial(context, tool, exc.code)
            return outcome(ToolStatus.DENIED, exc.code)
        except ToolUnavailable as exc:
            return outcome(ToolStatus.UNAVAILABLE, exc.code)
        except TimeoutError:
            logger.warning("Agent tool timed out", extra=_log_fields(tool, "tool_timeout"))
            return outcome(ToolStatus.FAILED, "tool_timeout")
        except Exception:
            # Internal log only; the caller sees a stable code, never the exception text.
            logger.exception("Agent tool failed", extra=_log_fields(tool, "tool_failed"))
            return outcome(ToolStatus.FAILED, "tool_failed")

        # Credentials in tool output never reach the agent, the model or stored metadata.
        data, redactions = self._redactor.redact_data(data)
        if redactions:
            logger.warning(
                "Sensitive values redacted from tool output",
                extra={
                    "event": "ai_secret_redacted",
                    "trust_level": "tool_result",
                    "tool": tool.name,
                    "redactions": redactions,
                },
            )
        return ToolResult(
            tool_name=tool.name,
            status=ToolStatus.SUCCESS,
            source_type=tool.source_type,
            # The only place a live check is asserted: a live-data tool that succeeded.
            live_check_performed=tool.source_type is SourceType.LIVE_BTP,
            data=data,
        )


def _result(
    name: str, status: ToolStatus, source: SourceType | None, code: str
) -> ToolResult:
    return ToolResult(tool_name=name, status=status, source_type=source, error_code=code)


def _log_denial(tool_name: str, code: str) -> None:
    logger.warning(
        "Agent tool denied",
        extra={"event": "ai_tool_authorization_denied", "tool": tool_name, "error_code": code},
    )


def _log_fields(tool: Tool, code: str) -> dict[str, str]:
    return {"event": "agent_tool_failed", "reason": tool.name, "error_code": code}


async def _audit_denial(context: AgentContext, tool: Tool, reason: str) -> None:
    if context.session_factory is None:
        return
    await audit_service.record_standalone(
        context.session_factory,
        AuditEventType.ACCESS_DENIED,
        Outcome.DENIED,
        actor_user_id=context.principal.user_id,
        details={
            "reason": reason,
            "tool": tool.name,
            "permission": tool.required_permission.value,
        },
    )
