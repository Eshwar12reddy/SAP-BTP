"""Build the stable agent response shape and backend-owned evidence."""

from __future__ import annotations

from collections.abc import Sequence

from app.agents.contracts import (
    AgentOutcome,
    AgentResponse,
    RoutingDecision,
)
from app.agents.evidence import build_evidence
from app.tools.contracts import SourceType, ToolResult


class AgentResponseBuilder:
    """Keep response formatting separate from routing and tool execution."""

    def build(
        self,
        decision: RoutingDecision,
        outcome: AgentOutcome,
        body: str,
        sources: Sequence[SourceType],
        tool_results: Sequence[ToolResult],
        llm_calls: int,
        *,
        footer: str | None = None,
        model: str | None = None,
    ) -> AgentResponse:
        content = f"{body}\n\n— {footer}" if footer else body
        return AgentResponse(
            content=content,
            route=decision.route,
            routing_method=decision.method,
            outcome=outcome,
            sources=list(sources),
            tool_results=list(tool_results),
            llm_calls=llm_calls,
            model=model,
            evidence=build_evidence(sources, tool_results),
        )
