"""Bounded, serializable contracts for multi-step BTP investigations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FindingSeverity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingConfidence(StrEnum):
    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    POSSIBLE = "POSSIBLE"
    UNKNOWN = "UNKNOWN"


class InvestigationStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    evidence_source: str = Field(min_length=1, max_length=120)
    read_only: bool = True


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    investigation_type: str = Field(min_length=1, max_length=80)
    target_ref: str | None = Field(default=None, max_length=200)
    steps: list[InvestigationStep] = Field(default_factory=list, max_length=12)
    bounded: bool = True


class InvestigationFinding(BaseModel):
    """Stable frontend/graph shape adapted from Phase 13 diagnosis findings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str = Field(min_length=1, max_length=240)
    rule_id: str = Field(min_length=1, max_length=100)
    resource_ref: str = Field(min_length=1, max_length=160)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    observed_at: datetime
    severity: FindingSeverity
    confidence: FindingConfidence
    explanation: str = Field(min_length=1, max_length=800)
    possible_causes: list[str] = Field(default_factory=list, max_length=8)
    next_steps: list[str] = Field(default_factory=list, max_length=8)
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)

