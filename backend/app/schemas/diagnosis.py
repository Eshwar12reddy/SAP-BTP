"""Typed, secret-free contracts for deterministic operational diagnosis."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DiagnosisConfidence = Literal["CONFIRMED", "LIKELY", "POSSIBLE", "UNKNOWN"]
DiagnosisSeverity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


class EvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_id: str = Field(min_length=1, max_length=200)
    source: Literal["cloud_foundry_v3"]
    resource_id: str = Field(min_length=1, max_length=160)
    observed_at: datetime
    summary: str = Field(max_length=500)


class ProviderFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["cloud_foundry_v3"]
    operation: str = Field(min_length=1, max_length=80)
    error_code: str = Field(min_length=1, max_length=80)
    observed_at: datetime


class DiagnosisFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    finding_id: str = Field(min_length=1, max_length=240)
    resource_id: str = Field(min_length=1, max_length=160)
    rule_id: str = Field(min_length=1, max_length=100)
    severity: DiagnosisSeverity
    observation: str = Field(min_length=1, max_length=800)
    evidence_references: list[str] = Field(default_factory=list, max_length=20)
    timestamp: datetime
    confidence: DiagnosisConfidence
    possible_causes: list[str] = Field(default_factory=list, max_length=8)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=8)
    resource_type: str = "cloud_foundry_resource"
    environment_id: uuid.UUID | None = None
    source_type: str = "LIVE_PROVIDER"
    calculation_details: dict[str, object] | None = None
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)
    data_freshness: str = "observed_now"
    diagnostic_status: str = "OBSERVED"


class ApplicationRuntimeSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    display_name: str
    state: str | None
    lifecycle: str | None
    space_id: str | None
    requested_instances: int | None = Field(default=None, ge=0)
    observed_instances: int | None = Field(default=None, ge=0)
    running_instances: int | None = Field(default=None, ge=0)
    process_count: int | None = Field(default=None, ge=0)
    event_count: int | None = Field(default=None, ge=0)
    service_instance_count: int | None = Field(default=None, ge=0)
    service_binding_count: int | None = Field(default=None, ge=0)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_limit_bytes: int | None = Field(default=None, ge=0)
    memory_utilization_percent: float | None = Field(default=None, ge=0, le=100)
    cpu_measurements_available: bool = False
    evidence_freshness: str = "observed_now"


class ApplicationDiagnosisRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: str
    environment_id: uuid.UUID
    generated_at: datetime
    summary: ApplicationRuntimeSummary
    findings: list[DiagnosisFinding] = Field(max_length=50)
    evidence: list[EvidenceReference] = Field(max_length=500)
    provider_failures: list[ProviderFailure] = Field(max_length=50)
    missing_information: list[str] = Field(max_length=30)
    root_cause_established: bool = False
    root_cause_statement: str = Field(max_length=500)
    ai_explanation: str | None = Field(default=None, max_length=6000)
    diagnostic_status: str = "COMPLETE"
    source_type: str = "LIVE_PROVIDER"
    evidence_freshness: str = "observed_now"


class HealthOverviewRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: uuid.UUID
    generated_at: datetime
    diagnostic_status: str
    applications_checked: int = Field(ge=0)
    findings: list[DiagnosisFinding] = Field(max_length=200)
    evidence: list[EvidenceReference] = Field(max_length=1000)
    provider_failures: list[ProviderFailure] = Field(max_length=100)
    missing_information: list[str] = Field(max_length=100)
    evidence_freshness: str = "observed_now"


class QuotaDiagnosisRead(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: uuid.UUID
    generated_at: datetime
    resource_id: str
    resource_type: str
    quota_name: str
    quota_type: str
    diagnostic_status: str
    findings: list[DiagnosisFinding] = Field(max_length=50)
    evidence: list[EvidenceReference] = Field(max_length=200)
    provider_failures: list[ProviderFailure] = Field(max_length=50)
    missing_information: list[str] = Field(max_length=30)


class UnusedResourceCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str
    resource_type: str
    display_name: str
    status: str = "POTENTIALLY_UNUSED_REQUIRES_VERIFICATION"
    confidence: DiagnosisConfidence = "POSSIBLE"
    limitation: str
    recommended_check: str
    observed_at: datetime
    evidence_references: list[str] = Field(default_factory=list, max_length=20)


class UnusedResourcePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: uuid.UUID
    generated_at: datetime
    items: list[UnusedResourceCandidate] = Field(max_length=100)
    diagnostic_status: str
    missing_information: list[str] = Field(max_length=30)
