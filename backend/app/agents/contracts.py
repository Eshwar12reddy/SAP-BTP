"""Typed contracts for the single agent."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.authorization import Principal
from app.core.config import Settings
from app.integrations.openai_embeddings import OpenAIEmbeddingsClient
from app.integrations.sap_btp.provider import BTPProvider
from app.safety.redaction import Redactor
from app.schemas.message import MESSAGE_MAX_LENGTH
from app.tools.contracts import SourceType, ToolResult


class Route(enum.StrEnum):
    KNOWLEDGE_SEARCH = "knowledge_search"
    GENERAL_KNOWLEDGE = "general_knowledge"
    DOCUMENTATION = "documentation"
    BTP_ACCOUNT = "btp_account"
    BTP_APPLICATIONS = "btp_applications"
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
    session_factory: async_sessionmaker[AsyncSession] | None = None
    settings: Settings | None = None
    embedding_client: OpenAIEmbeddingsClient | None = None
    redactor: Redactor | None = None
    # The conversation's BTP context, already re-authorized by the caller.
    btp_account_id: uuid.UUID | None = None
    btp_environment_id: uuid.UUID | None = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    route: Route
    routing_method: RoutingMethod
    outcome: AgentOutcome
    sources: list[SourceType]
    tool_results: list[ToolResult] = []
    llm_calls: int = 0
    model: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def live_check_performed(self) -> bool:
        """Derived from tool results only, so it cannot be asserted independently."""
        return any(result.live_check_performed for result in self.tool_results)
