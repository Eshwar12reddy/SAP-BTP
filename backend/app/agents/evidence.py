"""Backend-owned evidence references for agent responses."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.tools.contracts import SourceType, ToolResult, ToolStatus


class EvidenceConfidence(StrEnum):
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    UNKNOWN = "unknown"


class EvidenceReference(BaseModel):
    """A bounded, non-secret description of where an answer's evidence came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_type: SourceType
    label: str = Field(min_length=1, max_length=160)
    tool_name: str | None = Field(default=None, max_length=64)
    live_check_performed: bool = False
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN


def build_evidence(
    sources: Iterable[SourceType], tool_results: Iterable[ToolResult]
) -> list[EvidenceReference]:
    """Derive evidence only from server-controlled source and tool metadata."""

    references = [
        EvidenceReference(source_type=source, label=source.value.replace("_", " "))
        for source in dict.fromkeys(sources)
    ]
    references.extend(
        EvidenceReference(
            source_type=result.source_type,
            label=f"tool: {result.tool_name}",
            tool_name=result.tool_name,
            live_check_performed=result.live_check_performed,
            confidence=(
                EvidenceConfidence.CONFIRMED
                if result.status is ToolStatus.SUCCESS
                else EvidenceConfidence.UNKNOWN
            ),
        )
        for result in tool_results
        if result.source_type is not None and result.status is ToolStatus.SUCCESS
    )
    return references
