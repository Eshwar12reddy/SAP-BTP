"""Typed contracts shared by every registered tool.

A tool declares what it needs (permission, input and output models) and what kind of
information it produces (``source_type``). Whether a live check happened is decided by the
registry from that declaration and the actual outcome; tools and the model cannot set it.
"""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from app.auth.permissions import Permission

if TYPE_CHECKING:
    from app.agents.contracts import AgentContext


class ToolStatus(enum.StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"
    INVALID_INPUT = "invalid_input"
    FAILED = "failed"


class SourceType(enum.StrEnum):
    MODEL_KNOWLEDGE = "model_knowledge"
    STORED_METADATA = "stored_metadata"
    DOCUMENTATION = "documentation"
    KNOWLEDGE_DOCUMENTS = "knowledge_documents"
    CALCULATION = "calculation"
    LIVE_BTP = "live_btp"


class OperationType(enum.StrEnum):
    READ = "read"
    WRITE = "write"


class ToolInput(BaseModel):
    """Base for tool inputs: unknown fields are rejected, never ignored."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolOutput(BaseModel):
    model_config = ConfigDict(frozen=True)


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: str
    status: ToolStatus
    source_type: SourceType | None
    live_check_performed: bool = False
    data: dict[str, Any] | None = None
    error_code: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolUnavailable(Exception):
    """The tool cannot do its job right now (e.g. no live integration configured)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ToolDenied(Exception):
    """The caller may not access the requested resource. Same outcome as 'not found'."""

    def __init__(self, code: str = "not_found_or_not_authorized") -> None:
        super().__init__(code)
        self.code = code


ToolFunction = Callable[[Any, "AgentContext"], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    required_permission: Permission
    operation_type: OperationType
    source_type: SourceType
    run: ToolFunction
    timeout_seconds: float = 10.0
