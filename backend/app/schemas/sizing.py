"""Typed, provenance-aware contracts for deterministic resource sizing."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryUnit = Literal["BYTES", "MB", "MiB", "GB", "GiB"]
SizingRuntime = Literal["cloudfoundry", "kyma", "abap", "hana-cloud", "integration", "unknown"]
InputProvenance = Literal["USER_PROVIDED", "OBSERVED", "DERIVED", "ASSUMED", "UNKNOWN"]
QuotaState = Literal["FINITE", "UNLIMITED", "UNKNOWN", "ZERO"]


class SizingBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SizingAssumption(SizingBase):
    assumption_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    statement: str = Field(min_length=1, max_length=500)
    provenance: InputProvenance
    requires_confirmation: bool = True


class SizingProcessInput(SizingBase):
    process_type: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    instance_count: int = Field(ge=0, le=1_000_000)
    memory_per_instance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    memory_unit: MemoryUnit = "MB"
    disk_per_instance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    disk_unit: MemoryUnit = "MB"
    provenance: InputProvenance = "USER_PROVIDED"
    replace_existing: bool = False


class SizingRequirements(SizingBase):
    architecture_proposal_id: uuid.UUID | None = None
    architecture_proposal_revision: int | None = Field(default=None, ge=1)
    target_environment_id: uuid.UUID | None = None
    runtime_type: SizingRuntime = "unknown"
    application_name: str | None = Field(default=None, max_length=160)
    user_count: int | None = Field(default=None, ge=0, le=100_000_000)
    concurrent_users: int | None = Field(default=None, ge=0, le=100_000_000)
    peak_requests_per_second: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    sustainable_requests_per_second_per_instance: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    measured_memory_per_instance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    measured_memory_unit: MemoryUnit = "MB"
    configured_memory_per_instance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    configured_memory_unit: MemoryUnit = "MB"
    instance_count: int | None = Field(default=None, ge=0, le=1_000_000)
    minimum_redundancy_instances: int | None = Field(default=None, ge=0, le=1_000_000)
    configured_disk_per_instance: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    configured_disk_unit: MemoryUnit = "MB"
    data_volume: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    data_volume_unit: MemoryUnit = "GB"
    expected_growth_per_period: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    growth_unit: MemoryUnit = "GB"
    growth_period_days: int | None = Field(default=None, gt=0, le=36_500)
    projection_period_days: int | None = Field(default=None, gt=0, le=36_500)
    expected_records: int | None = Field(default=None, ge=0, le=10_000_000_000)
    average_stored_bytes_per_record: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    index_overhead_factor: Decimal | None = Field(default=None, ge=1, le=100, max_digits=10, decimal_places=6)
    backup_factor: Decimal | None = Field(default=None, ge=1, le=100, max_digits=10, decimal_places=6)
    replication_factor: Decimal | None = Field(default=None, ge=1, le=100, max_digits=10, decimal_places=6)
    retention_period_days: int | None = Field(default=None, gt=0, le=36_500)
    safety_margin_percent: Decimal = Field(default=Decimal(0), ge=0, le=1000, max_digits=8, decimal_places=4)
    existing_memory_allocation: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    existing_memory_unit: MemoryUnit = "MB"
    existing_instance_count: int | None = Field(default=None, ge=0, le=1_000_000)
    resize_existing: bool = False
    process_types: list[SizingProcessInput] = Field(default_factory=list, max_length=20)
    quota_memory_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    quota_memory_usage: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    quota_memory_unit: MemoryUnit = "MB"
    quota_memory_state: QuotaState = "UNKNOWN"
    quota_instance_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    quota_instance_usage: int | None = Field(default=None, ge=0, le=1_000_000)
    quota_instance_state: QuotaState = "UNKNOWN"
    quota_disk_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    quota_disk_usage: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    quota_disk_unit: MemoryUnit = "MB"
    quota_disk_state: QuotaState = "UNKNOWN"
    quota_environment_id: uuid.UUID | None = None
    quota_source: Literal["USER_PROVIDED", "LIVE_PROVIDER", "UNKNOWN"] = "UNKNOWN"
    quota_observed_at: datetime | None = None
    workload_measurement_source: str | None = Field(default=None, max_length=300)
    measurement_timestamp: datetime | None = None
    user_assumptions: list[SizingAssumption] = Field(default_factory=list, max_length=30)
    include_live_quota: bool = False

    @model_validator(mode="after")
    def _validate_quota_semantics(self) -> SizingRequirements:
        for state, limit, usage in (
            (self.quota_memory_state, self.quota_memory_limit, self.quota_memory_usage),
            (self.quota_disk_state, self.quota_disk_limit, self.quota_disk_usage),
        ):
            if state == "FINITE" and limit is None:
                raise ValueError("finite quota requires a limit")
            if state == "FINITE" and usage is not None and limit is not None and usage < 0:
                raise ValueError("quota usage cannot be negative")
        if self.quota_instance_state == "FINITE" and self.quota_instance_limit is None:
            raise ValueError("finite instance quota requires a limit")
        if self.projection_period_days is not None and self.expected_growth_per_period is not None and self.growth_period_days is None:
            raise ValueError("growth_period_days is required with expected_growth_per_period")
        if self.expected_records is not None and self.average_stored_bytes_per_record is None:
            raise ValueError("average_stored_bytes_per_record is required with expected_records")
        return self


class SizingRequirementsAnalysis(SizingBase):
    requirements: SizingRequirements
    missing_inputs: list[str] = Field(default_factory=list, max_length=40)
    clarification_questions: list[str] = Field(default_factory=list, max_length=12)
    explicit_inputs: list[str] = Field(default_factory=list, max_length=40)
    analysis_status: Literal["READY", "NEEDS_CLARIFICATION"]


class SizingFormula(SizingBase):
    formula_id: str
    formula_version: str
    expression: str
    description: str


class SizingIntermediate(SizingBase):
    name: str
    value: Decimal | None = None
    unit: str
    provenance: InputProvenance


class SizingCalculation(SizingBase):
    calculation_id: str
    resource_reference: str
    calculation_type: Literal["CONFIGURED_MEMORY", "OBSERVED_MEMORY", "INSTANCE_REQUIREMENT", "CONFIGURED_DISK", "LOGICAL_STORAGE", "PROVISIONED_STORAGE", "BACKUP_STORAGE", "REPLICATED_CAPACITY"]
    input_values: dict[str, str | int | float | None]
    input_units: dict[str, str]
    input_provenance: dict[str, InputProvenance]
    formula: SizingFormula
    intermediate_calculations: list[SizingIntermediate] = Field(default_factory=list, max_length=30)
    final_value: Decimal | None = None
    result_unit: str
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    safety_margin_percent: Decimal = Decimal(0)
    additional_capacity: Decimal | None = None
    evidence_references: list[str] = Field(default_factory=list, max_length=20)
    observation_time: datetime | None = None
    verification_status: Literal["CALCULATED", "OBSERVED", "PARTIAL", "UNKNOWN"]
    warnings: list[str] = Field(default_factory=list, max_length=20)
    missing_inputs: list[str] = Field(default_factory=list, max_length=20)
    scenario_id: str


class SizingQuotaImpact(SizingBase):
    dimension: Literal["MEMORY", "INSTANCES", "DISK"]
    quota_state: QuotaState
    quota_limit: Decimal | None
    quota_unit: str
    existing_usage: Decimal | None
    additional_allocation: Decimal | None
    projected_usage: Decimal | None
    remaining_capacity: Decimal | None
    projected_utilization_percent: Decimal | None
    compatible: bool | None
    evidence_references: list[str] = Field(default_factory=list, max_length=10)
    observation_time: datetime | None = None
    warnings: list[str] = Field(default_factory=list, max_length=12)


class SizingResult(SizingBase):
    result_id: uuid.UUID
    scenario_id: str
    formula_version: str
    runtime_type: SizingRuntime
    application_name: str | None
    architecture_proposal_id: uuid.UUID | None
    architecture_proposal_revision: int | None
    calculations: list[SizingCalculation] = Field(default_factory=list, max_length=100)
    quota_impacts: list[SizingQuotaImpact] = Field(default_factory=list, max_length=20)
    assumptions: list[SizingAssumption] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=40)
    missing_inputs: list[str] = Field(default_factory=list, max_length=40)
    status: Literal["COMPLETE", "PARTIAL", "UNSUPPORTED", "NEEDS_INPUT"]
    generated_at: datetime


class SizingCalculationRequest(SizingBase):
    request_text: str | None = Field(default=None, min_length=1, max_length=8_000)
    requirements: SizingRequirements | None = None
    scenario_id: str = Field(default="baseline", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]{0,79}$")
    persist: bool = True

    @model_validator(mode="after")
    def _has_input(self) -> SizingCalculationRequest:
        if self.request_text is None and self.requirements is None:
            raise ValueError("request_text or requirements is required")
        return self


class SizingScenario(SizingBase):
    scenario_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    requirements: SizingRequirements


class SizingComparisonRequest(SizingBase):
    scenarios: list[SizingScenario] = Field(min_length=1, max_length=8)


class SizingComparisonResponse(SizingBase):
    comparison_id: uuid.UUID
    formula_version: str
    results: list[SizingResult]
    compared_dimensions: list[str]
    generated_at: datetime


class SizingResultPage(SizingBase):
    items: list[SizingResult]
    has_more: bool
