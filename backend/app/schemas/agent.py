from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import AgentOutcome, AgentResponse, Route, RoutingMethod
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
