"""Serializable, bounded state contracts for one agent run.

These models intentionally contain identifiers and authorization facts only.  Runtime
objects such as SQLAlchemy sessions, principals, providers and clients stay in
``AgentContext`` and are never placed in serializable agent state.
"""

from __future__ import annotations

import json
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.errors import AgentError
from app.agents.evidence import EvidenceReference
from app.agents.investigation import InvestigationFinding, InvestigationPlan


class InvestigationStatus(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class GraphStep(StrEnum):
    VALIDATE_REQUEST = "validate_request"
    RESOLVE_AUTHORIZED_CONTEXT = "resolve_authorized_context"
    CLASSIFY_INTENT = "classify_intent"
    BUILD_INVESTIGATION_PLAN = "build_investigation_plan"
    SELECT_AUTHORIZED_TOOLS = "select_authorized_tools"
    REQUEST_APPROVAL = "request_approval"
    EXECUTE_READ_ONLY_TOOLS = "execute_read_only_tools"
    EVALUATE_RESULTS = "evaluate_results"
    BUILD_RESPONSE = "build_response"
    PERSIST_COMPLETION = "persist_completion"
    CANCELED = "canceled"


class AgentGraphState(BaseModel):
    """The only persisted LangGraph state contract.

    Runtime authorization objects and clients are supplied through LangGraph's runtime
    context and are intentionally absent from this model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=32_000)
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None
    context_message_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    memory_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    status: InvestigationStatus = InvestigationStatus.NOT_STARTED
    current_step: GraphStep = GraphStep.VALIDATE_REQUEST
    completed_steps: list[GraphStep] = Field(default_factory=list, max_length=20)
    pending_steps: list[GraphStep] = Field(default_factory=list, max_length=20)
    route: str | None = Field(default=None, max_length=64)
    routing_method: str | None = Field(default=None, max_length=16)
    plan: list[str] = Field(default_factory=list, max_length=20)
    investigation_plan: InvestigationPlan | None = None
    tool_results: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=40)
    findings: list[InvestigationFinding] = Field(default_factory=list, max_length=50)
    missing_evidence: list[str] = Field(default_factory=list, max_length=30)
    recommendations: list[str] = Field(default_factory=list, max_length=30)
    approval_reference: str | None = Field(default=None, max_length=80)
    failure: AgentError | None = None
    response: dict[str, Any] | None = None
    final_response_reference: str | None = Field(default=None, max_length=255)
    iteration: int = Field(default=0, ge=0, le=50)
    llm_calls: int = Field(default=0, ge=0, le=20)
    tool_calls: int = Field(default=0, ge=0, le=20)

    @field_validator("response")
    @classmethod
    def _bounded_response(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
        except (TypeError, ValueError):
            raise ValueError("response must be JSON serializable") from None
        if size > 64_000:
            raise ValueError("response is too large for graph state")
        return value

    @field_validator("tool_results")
    @classmethod
    def _bounded_tool_results(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
        except (TypeError, ValueError):
            raise ValueError("tool results must be JSON serializable") from None
        if size > 32_000:
            raise ValueError("tool results are too large for graph state")
        return value

    @model_validator(mode="after")
    def _bounded_checkpoint(self) -> AgentGraphState:
        # This hard upper bound protects the saver even when a deployment uses a larger
        # configured request limit.  The setting may choose a lower operational limit.
        if len(json.dumps(self.model_dump(mode="json"), separators=(",", ":"))) > 256 * 1024:
            raise ValueError("graph checkpoint is too large")
        return self


class AgentRunContext(BaseModel):
    """Safe state that may be logged, traced or passed between orchestration stages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None


class InvestigationContext(BaseModel):
    """Bounded target context for a read-only investigation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    environment_id: uuid.UUID | None = None
    application_id: str | None = Field(default=None, max_length=160)
    evidence_keys: tuple[str, ...] = Field(default_factory=tuple, max_length=20)

    @field_validator("application_id")
    @classmethod
    def _application_id_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("application_id must not be blank")
        return value
