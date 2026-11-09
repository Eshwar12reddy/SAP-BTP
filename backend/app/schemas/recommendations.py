"""Typed contracts for SAP BTP service recommendations.

The contracts deliberately keep documented suitability separate from account-specific
availability.  A service can be a good documented fit while entitlement, region and
quota remain unknown.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Status = Literal["SUPPORTED", "UNSUPPORTED", "UNKNOWN", "NOT_REQUESTED", "NOT_CONFIGURED", "NOT_CHECKED"]


class RecommendationBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceRequirements(RecommendationBase):
    business_objective: str | None = Field(default=None, max_length=2_000)
    application_type: str | None = Field(default=None, max_length=300)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    existing_sap_applications: list[str] = Field(default_factory=list, max_length=20)
    s4hana_integration_requirements: str | None = Field(default=None, max_length=1_000)
    runtime_preference: Literal["cloudfoundry", "kyma", "abap", "cloudfoundry_or_kyma", "unknown"] = "unknown"
    database_requirements: list[str] = Field(default_factory=list, max_length=20)
    identity_authentication_needs: list[str] = Field(default_factory=list, max_length=20)
    messaging_event_requirements: list[str] = Field(default_factory=list, max_length=20)
    workflow_requirements: list[str] = Field(default_factory=list, max_length=20)
    monitoring_requirements: list[str] = Field(default_factory=list, max_length=20)
    region: str | None = Field(default=None, max_length=100)
    availability_target: str | None = Field(default=None, max_length=300)
    security_compliance_constraints: list[str] = Field(default_factory=list, max_length=20)
    expected_workload: str | None = Field(default=None, max_length=500)
    budget_constraints: str | None = Field(default=None, max_length=500)
    existing_services: list[str] = Field(default_factory=list, max_length=20)
    commercial_model: str | None = Field(default=None, max_length=200)
    unknown_requirements: list[str] = Field(default_factory=list, max_length=20)
    explicit_assumptions: list[str] = Field(default_factory=list, max_length=20)
    excluded_services: list[str] = Field(default_factory=list, max_length=20)


class RequirementsAnalysis(RecommendationBase):
    requirements: ServiceRequirements
    extracted_capabilities: list[str] = Field(default_factory=list, max_length=20)
    missing_critical_information: list[str] = Field(default_factory=list, max_length=10)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    analysis_status: Literal["READY", "NEEDS_CLARIFICATION"]
    source: Literal["USER_MESSAGE", "AUTHORIZED_CONTEXT"] = "USER_MESSAGE"


class ServiceCatalogEntry(RecommendationBase):
    service_key: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    service_name: str = Field(min_length=2, max_length=160)
    technical_identifier: str | None = Field(default=None, max_length=160)
    capability_category: str
    supported_use_cases: list[str] = Field(min_length=1, max_length=12)
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    runtime_compatibility: dict[str, Literal["DOCUMENTED", "UNKNOWN"]] = Field(default_factory=dict)
    official_documentation_url: HttpUrl
    source_verification_timestamp: datetime
    catalog_version: str = Field(pattern=r"^v[0-9]+\.[0-9]+$")
    availability_verification_status: Literal["DOCUMENTED_REFERENCE", "REGION_UNKNOWN"]
    limitations: list[str] = Field(default_factory=list, max_length=12)
    alternative_service_keys: list[str] = Field(default_factory=list, max_length=8)


class LiveCatalogStatus(RecommendationBase):
    retrieval_status: Literal["NOT_REQUESTED", "NOT_CONFIGURED", "AVAILABLE", "PARTIAL", "UNAUTHORIZED"]
    environment_id: uuid.UUID | None = None
    observed_at: datetime | None = None
    provider: str | None = None
    notes: list[str] = Field(default_factory=list, max_length=10)


class RecommendationEvidence(RecommendationBase):
    source_type: Literal["DOCUMENTATION", "LIVE_PROVIDER", "CALCULATION", "USER_PROVIDED"]
    reference: str = Field(min_length=1, max_length=500)
    observed_at: datetime | None = None
    verification_status: Literal["VERIFIED", "NOT_VERIFIED", "PARTIAL"]


class ServiceRecommendation(RecommendationBase):
    recommendation_id: str = Field(pattern=r"^rec-[a-z0-9-]{3,100}$")
    service_name: str
    verified_identifier: str | None = None
    related_user_requirements: list[str] = Field(default_factory=list, max_length=12)
    recommended_capability: str
    suggested_plan: str | None = None
    suitability_explanation: str
    eligibility_status: Status
    runtime_status: Status
    region_status: Status
    entitlement_status: Literal["NOT_VERIFIED", "AVAILABLE_IN_PROVIDER_CATALOG", "UNKNOWN_ENTITLEMENT", "EXISTING_SERVICE_NOT_VERIFIED"]
    quota_status: Literal["NOT_CHECKED", "UNKNOWN", "SUFFICIENT", "INSUFFICIENT"]
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    relevant_alternatives: list[str] = Field(default_factory=list, max_length=8)
    tradeoffs: list[str] = Field(default_factory=list, max_length=12)
    evidence_references: list[RecommendationEvidence] = Field(default_factory=list, max_length=12)
    suitability_confidence: Literal["HIGH", "MEDIUM", "LOW"]
    availability_confidence: Literal["HIGH", "MEDIUM", "LOW", "NOT_ASSESSED"]
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    source_verification_time: datetime


class RecommendationRequest(RecommendationBase):
    request_text: str | None = Field(default=None, min_length=1, max_length=4_000)
    requirements: ServiceRequirements | None = None
    include_live_evidence: bool = False
    environment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _has_input(self) -> RecommendationRequest:
        if self.request_text is None and self.requirements is None:
            raise ValueError("request_text or requirements is required")
        return self


class RecommendationResponse(RecommendationBase):
    analysis: RequirementsAnalysis
    recommendations: list[ServiceRecommendation] = Field(default_factory=list, max_length=20)
    no_suitable_options: bool
    partial_evidence: bool
    catalog_version: str
    live_catalog: LiveCatalogStatus
    generated_at: datetime


class ServiceComparisonRequest(RecommendationBase):
    service_keys: list[str] = Field(min_length=2, max_length=8)
    requirements: ServiceRequirements | None = None
    request_text: str | None = Field(default=None, max_length=4_000)


class ServiceComparisonRow(RecommendationBase):
    service_key: str
    service_name: str
    capability_fit: str
    meaningful_difference: str
    runtime_status: Status
    region_status: Status
    entitlement_status: str
    prerequisites: list[str] = Field(default_factory=list, max_length=12)
    evidence_references: list[RecommendationEvidence] = Field(default_factory=list, max_length=12)


class ServiceComparisonResponse(RecommendationBase):
    rows: list[ServiceComparisonRow]
    criteria: list[str]
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    generated_at: datetime


class ServicePrerequisitesResponse(RecommendationBase):
    service: ServiceCatalogEntry
    prerequisites: list[str]
    limitations: list[str]
    alternatives: list[str]
    evidence_references: list[RecommendationEvidence]
