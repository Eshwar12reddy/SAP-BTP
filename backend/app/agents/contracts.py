"""Typed contracts for the single agent."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.context import ContextBundle
from app.agents.errors import AgentError
from app.agents.evidence import EvidenceConfidence, EvidenceReference
from app.agents.state import AgentRunContext, InvestigationContext
from app.auth.authorization import Principal
from app.core.config import Settings
from app.integrations.deepseek_client import DeepSeekClient
from app.integrations.openai_embeddings import OpenAIEmbeddingsClient
from app.integrations.sap_btp.provider import BTPProvider
from app.safety.redaction import Redactor
from app.schemas.message import MESSAGE_MAX_LENGTH
from app.tools.contracts import SourceType, ToolResult

__all__ = [
    "AgentContext",
    "AgentError",
    "AgentOutcome",
    "AgentRequest",
    "AgentResponse",
    "AgentRunContext",
    "EvidenceConfidence",
    "EvidenceReference",
    "InvestigationContext",
    "Route",
    "RoutingDecision",
    "RoutingMethod",
]


class Route(enum.StrEnum):
    KNOWLEDGE_SEARCH = "knowledge_search"
    GENERAL_KNOWLEDGE = "general_knowledge"
    DOCUMENTATION = "documentation"
    BTP_ACCOUNT = "btp_account"
    BTP_APPLICATIONS = "btp_applications"
    BTP_INVENTORY = "btp_inventory"
    BTP_HEALTH = "btp_health"
    BTP_RECOMMENDATIONS = "btp_recommendations"
    BTP_ARCHITECTURE = "btp_architecture"
    BTP_SIZING = "btp_sizing"
    BTP_INFRASTRUCTURE_PLAN = "btp_infrastructure_plan"
    BTP_DIAGNOSIS = "btp_diagnosis"
    ARCHITECTURE = "architecture"
    SIZING = "sizing"
    OPERATION_REQUEST = "operation_request"
    UNSUPPORTED = "unsupported"


class RoutingMethod(enum.StrEnum):
    RULE = "rule"
    MODEL = "model"


class AgentOutcome(enum.StrEnum):
    ANSWERED = "answered"
    NEEDS_CLARIFICATION = "needs_clarification"
    OPERATION_NOT_PERFORMED = "operation_not_performed"
    TOOL_UNAVAILABLE = "tool_unavailable"
    DENIED = "denied"
    FAILED = "failed"


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    route: Route
    method: RoutingMethod


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)
    conversation_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


@dataclass
class AgentContext:
    """Trusted, server-derived context. Never built from client-supplied roles or scopes."""

    principal: Principal
    session: AsyncSession
    btp_provider: BTPProvider
    # Phase 12 read-only connector, injected by the server and never selected by the model.
    btp_read_service: object | None = None
    # Phase 13 diagnosis facade over the same Phase 12 connector.
    btp_diagnosis_service: object | None = None
    btp_health_service: object | None = None
    btp_recommendation_service: object | None = None
    btp_architecture_service: object | None = None
    btp_sizing_service: object | None = None
    infrastructure_plan_service: object | None = None
    deepseek_client: DeepSeekClient | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    settings: Settings | None = None
    embedding_client: OpenAIEmbeddingsClient | None = None
    redactor: Redactor | None = None
    # The conversation's BTP context, already re-authorized by the caller.
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None
    # Assembled per request from owned conversation rows and authorized memories.
    assembled_context: ContextBundle | None = None
    current_message_id: uuid.UUID | None = None

    def serializable_state(self, conversation_id: uuid.UUID | None = None) -> AgentRunContext:
        """Return only safe identifiers; never serialize this runtime context directly."""

        return AgentRunContext(
            user_id=self.principal.user_id,
            conversation_id=conversation_id,
            btp_account_id=self.btp_account_id,
            btp_environment_id=self.btp_environment_id,
        )


class AgentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    route: Route
    routing_method: RoutingMethod
    outcome: AgentOutcome
    sources: list[SourceType]
    tool_results: list[ToolResult] = Field(default_factory=list)
    llm_calls: int = 0
    model: str | None = None
    # Evidence is available to orchestration and tracing, but excluded from the legacy API
    # metadata shape. ``sources`` and ``tool_results`` remain the public compatibility fields.
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def live_check_performed(self) -> bool:
        """Derived from tool results only, so it cannot be asserted independently."""
        return any(result.live_check_performed for result in self.tool_results)


# ``AgentResponse`` is the historical public name; ``AgentResult`` is the architecture-level
# contract name for new integrations and intentionally has identical serialization.
AgentResult = AgentResponse
__all__.append("AgentResult")
