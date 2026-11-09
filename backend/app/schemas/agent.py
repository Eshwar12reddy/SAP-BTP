from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import AgentOutcome, AgentResponse, Route, RoutingMethod
from app.agents.errors import AgentError
from app.agents.investigation import InvestigationFinding, InvestigationPlan
from app.agents.state import AgentGraphState, GraphStep, InvestigationStatus
from app.models.agent_approval import ApprovalDecision, ApprovalStatus
from app.schemas.message import MessageRead
from app.tools.contracts import SourceType, ToolStatus


class ToolResultRead(BaseModel):
    """A tool execution record. ``data`` is omitted; the reply text already presents it."""

    tool_name: str
    status: ToolStatus
    source_type: SourceType | None
    live_check_performed: bool
    error_code: str | None
    timestamp: datetime


class KnowledgeCitationRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: UUID
    source_version_id: UUID


class AgentReplyMetadata(BaseModel):
    """Stored on the assistant message (``agent_metadata``) and returned with it."""

    route: Route
    routing_method: RoutingMethod
    outcome: AgentOutcome
    sources: list[SourceType]
    live_check_performed: bool
    tool_results: list[ToolResultRead]
    llm_calls: int
    model: str | None
    knowledge_citations: list[KnowledgeCitationRef] = Field(default_factory=list)

    @classmethod
    def from_response(cls, response: AgentResponse) -> AgentReplyMetadata:
        # Tool ``data`` is dropped here (ToolResultRead ignores it).
        metadata = response.model_dump(exclude={"content"})
        citations = []
        for tool in response.tool_results:
            if tool.tool_name != "knowledge_search" or not tool.data:
                continue
            for item in tool.data.get("items", []):
                citation = item.get("citation", {}) if isinstance(item, dict) else {}
                if citation.get("source_id") and citation.get("source_version_id"):
                    citations.append({
                        "source_id": citation["source_id"],
                        "source_version_id": citation["source_version_id"],
                    })
        metadata["knowledge_citations"] = citations
        return cls.model_validate(metadata)


class AgentReplyRead(AgentReplyMetadata):
    message: MessageRead


class ReplyStatusRead(BaseModel):
    """Where the reply to one user message stands. Reading it never starts an AI call."""

    status: Literal["not_requested", "generating", "failed", "completed"]
    reply: AgentReplyRead | None = None


class InvestigationStatusRead(BaseModel):
    """Safe graph status; raw checkpoint payloads are never exposed."""

    thread_id: str
    status: InvestigationStatus
    current_step: GraphStep
    completed_steps: list[GraphStep]
    pending_steps: list[GraphStep]
    failure: AgentError | None = None
    final_response_reference: str | None = None
    iteration: int
    llm_calls: int
    tool_calls: int
    investigation_plan: InvestigationPlan | None = None
    findings: list[InvestigationFinding] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    pending_approval: ApprovalRead | None = None

    @classmethod
    def from_state(cls, thread_id: str, state: AgentGraphState | None, pending_approval: ApprovalRead | None = None) -> InvestigationStatusRead:
        if state is None:
            return cls(
                thread_id=thread_id,
                status=InvestigationStatus.NOT_STARTED,
                current_step=GraphStep.VALIDATE_REQUEST,
                completed_steps=[],
                pending_steps=[],
                iteration=0,
                llm_calls=0,
                tool_calls=0,
                pending_approval=pending_approval,
            )
        return cls(
            thread_id=thread_id,
            status=state.status,
            current_step=state.current_step,
            completed_steps=state.completed_steps,
            pending_steps=state.pending_steps,
            failure=state.failure,
            final_response_reference=state.final_response_reference,
            iteration=state.iteration,
            llm_calls=state.llm_calls,
            tool_calls=state.tool_calls,
            investigation_plan=state.investigation_plan,
            findings=state.findings,
            missing_evidence=state.missing_evidence,
            recommendations=state.recommendations,
            pending_approval=pending_approval,
        )


class ApprovalRead(BaseModel):
    id: UUID
    operation_type: str
    operation_summary: str
    risk_level: str
    status: ApprovalStatus
    execution_available: bool
    expires_at: datetime
    created_at: datetime
    decision: ApprovalDecision | None = None
    decision_at: datetime | None = None
    result_summary: str | None = None


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ApprovalDecision


class ApprovalDecisionRead(BaseModel):
    approval: ApprovalRead
    message: str
    response: str | None = None
