"""Typed, validated contracts for SAP BTP architecture proposals."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class ArchitectureBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequirementSource(ArchitectureBase):
    classification: Literal["EXPLICIT_REQUIREMENT", "VERIFIED_CONTEXT", "ASSUMPTION", "UNKNOWN"]
    value: str = Field(min_length=1, max_length=1_000)
    evidence_references: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureRequirements(ArchitectureBase):
    project_name: str | None = Field(default=None, max_length=200)
    business_objective: str | None = Field(default=None, max_length=2_000)
    application_type: str | None = Field(default=None, max_length=300)
    user_count: int | None = Field(default=None, ge=0, le=10_000_000)
    user_types: list[str] = Field(default_factory=list, max_length=20)
    existing_sap_systems: list[str] = Field(default_factory=list, max_length=20)
    external_systems: list[str] = Field(default_factory=list, max_length=20)
    integration_requirements: list[str] = Field(default_factory=list, max_length=20)
    database_requirements: list[str] = Field(default_factory=list, max_length=20)
    runtime_preference: Literal["cloudfoundry", "kyma", "abap", "cloudfoundry_or_kyma", "unknown"] = "unknown"
    authentication_requirements: list[str] = Field(default_factory=list, max_length=20)
    authorization_requirements: list[str] = Field(default_factory=list, max_length=20)
    data_sensitivity: str | None = Field(default=None, max_length=300)
    compliance_constraints: list[str] = Field(default_factory=list, max_length=20)
    preferred_region: str | None = Field(default=None, max_length=100)
    availability_objectives: str | None = Field(default=None, max_length=500)
    recovery_objectives: str | None = Field(default=None, max_length=500)
    expected_traffic: str | None = Field(default=None, max_length=500)
    workload_assumptions: list[str] = Field(default_factory=list, max_length=20)
    existing_services: list[str] = Field(default_factory=list, max_length=20)
    budget_constraints: str | None = Field(default=None, max_length=500)
    account_environment_context: str | None = Field(default=None, max_length=500)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    explicit_requirements: list[RequirementSource] = Field(default_factory=list, max_length=30)
    verified_context: list[RequirementSource] = Field(default_factory=list, max_length=20)
    assumptions: list[RequirementSource] = Field(default_factory=list, max_length=20)
    unknown_requirements: list[RequirementSource] = Field(default_factory=list, max_length=20)


class ArchitectureRequirementsAnalysis(ArchitectureBase):
    requirements: ArchitectureRequirements
    capability_keys: list[str] = Field(default_factory=list, max_length=20)
    missing_critical_information: list[str] = Field(default_factory=list, max_length=20)
    clarification_questions: list[str] = Field(default_factory=list, max_length=8)
    analysis_status: Literal["READY", "NEEDS_CLARIFICATION"]


class ArchitectureRuntimeOption(ArchitectureBase):
    runtime_id: str
    name: str
    suitability: Literal["SELECTED", "ALTERNATIVE", "NOT_SUITABLE", "UNKNOWN"]
    rationale: str
    prerequisites: list[str] = Field(default_factory=list, max_length=10)
    operational_considerations: list[str] = Field(default_factory=list, max_length=10)
    restrictions: list[str] = Field(default_factory=list, max_length=10)
    evidence_references: list[str] = Field(default_factory=list, max_length=8)
    verification_status: Literal["DOCUMENTED", "UNKNOWN"]


class ArchitectureComponent(ArchitectureBase):
    component_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=160)
    component_type: Literal["ACTOR", "APPLICATION", "SERVICE", "SAP_SYSTEM", "EXTERNAL_SYSTEM", "DATA_STORE", "ENVIRONMENT"]
    purpose: str = Field(min_length=1, max_length=1_000)
    related_service_key: str | None = Field(default=None, max_length=80)
    environment_id: str | None = Field(default=None, max_length=80)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    evidence_references: list[str] = Field(default_factory=list, max_length=8)
    verification_status: Literal["DOCUMENTED", "PROPOSED", "USER_PROVIDED", "UNKNOWN"]


class ArchitectureConnection(ArchitectureBase):
    connection_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    source_component_id: str
    target_component_id: str
    purpose: str = Field(min_length=1, max_length=500)
    communication_pattern: Literal["HTTPS_API", "DESTINATION_BASED", "CLOUD_CONNECTOR", "EVENT", "QUEUE", "WORKFLOW", "INTERNAL", "UNKNOWN"]
    security_consideration: str = Field(min_length=1, max_length=500)
    verification_status: Literal["DOCUMENTED", "PROPOSED", "UNKNOWN"]


class ArchitectureDataFlow(ArchitectureBase):
    flow_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    source_component_id: str
    target_component_id: str
    data_description: str = Field(min_length=1, max_length=500)
    direction: Literal["REQUEST_RESPONSE", "PUBLISH_SUBSCRIBE", "COMMAND", "REPLICATION", "UNKNOWN"]
    consistency: Literal["SYNCHRONOUS", "EVENTUAL", "UNKNOWN"]
    data_protection: str = Field(min_length=1, max_length=500)


class ArchitectureEnvironment(ArchitectureBase):
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=160)
    stage: Literal["DEVELOPMENT", "TEST", "PRODUCTION", "SHARED"]
    runtime_id: str
    purpose: str = Field(min_length=1, max_length=500)
    account_structure: str = Field(min_length=1, max_length=500)
    status: Literal["PROPOSED", "VERIFIED"]
    evidence_references: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureSecurityControl(ArchitectureBase):
    control_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    control: str = Field(min_length=1, max_length=300)
    applies_to_component_ids: list[str] = Field(default_factory=list, max_length=20)
    recommendation: str = Field(min_length=1, max_length=800)
    implementation_status: Literal["RECOMMENDED", "NOT_IMPLEMENTED", "VERIFIED"] = "RECOMMENDED"
    evidence_references: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureAssumption(ArchitectureBase):
    assumption_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    statement: str = Field(min_length=1, max_length=500)
    impact: Literal["LOW", "MEDIUM", "HIGH"]
    requires_confirmation: bool = True


class ArchitectureRisk(ArchitectureBase):
    risk_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    risk: str = Field(min_length=1, max_length=500)
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    mitigation: str = Field(min_length=1, max_length=800)
    evidence_references: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureAlternative(ArchitectureBase):
    alternative_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str = Field(min_length=1, max_length=200)
    change_from_primary: str = Field(min_length=1, max_length=500)
    benefits: list[str] = Field(default_factory=list, max_length=8)
    drawbacks: list[str] = Field(default_factory=list, max_length=8)
    prerequisites: list[str] = Field(default_factory=list, max_length=8)
    reliability_security_implications: list[str] = Field(default_factory=list, max_length=8)
    evidence_references: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureEvidence(ArchitectureBase):
    evidence_id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    title: str = Field(min_length=1, max_length=200)
    url: HttpUrl
    evidence_type: Literal["OFFICIAL_DOCUMENTATION", "LIVE_PROVIDER", "CALCULATION", "USER_PROVIDED"]
    verification_status: Literal["VERIFIED", "NOT_VERIFIED", "PARTIAL"]
    observed_at: datetime | None = None
    supports: list[str] = Field(default_factory=list, max_length=12)


class ArchitectureValidationFinding(ArchitectureBase):
    rule_id: str
    severity: Literal["INFO", "WARNING", "ERROR"]
    message: str
    related_ids: list[str] = Field(default_factory=list, max_length=12)


class ArchitectureValidation(ArchitectureBase):
    valid: bool
    findings: list[ArchitectureValidationFinding] = Field(default_factory=list, max_length=50)
    validated_at: datetime


class ArchitectureDiagram(ArchitectureBase):
    format: Literal["MERMAID_FLOWCHART"] = "MERMAID_FLOWCHART"
    source: Literal["SERVER_GENERATED"] = "SERVER_GENERATED"
    mermaid: str = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def _safe_mermaid(self) -> ArchitectureDiagram:
        lowered = self.mermaid.lower()
        forbidden = ("javascript:", "<script", "click ", "%%{", "init:", "html")
        if any(token in lowered for token in forbidden):
            raise ValueError("diagram contains an unsupported or unsafe construct")
        if not lowered.startswith("flowchart "):
            raise ValueError("diagram must be a flowchart")
        return self


class ArchitectureProposal(ArchitectureBase):
    proposal_id: uuid.UUID
    revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    overview: str = Field(min_length=1, max_length=4_000)
    requirements: ArchitectureRequirements
    runtime_options: list[ArchitectureRuntimeOption] = Field(default_factory=list, max_length=8)
    components: list[ArchitectureComponent] = Field(min_length=1, max_length=50)
    connections: list[ArchitectureConnection] = Field(default_factory=list, max_length=100)
    data_flows: list[ArchitectureDataFlow] = Field(default_factory=list, max_length=100)
    environments: list[ArchitectureEnvironment] = Field(default_factory=list, max_length=12)
    security_controls: list[ArchitectureSecurityControl] = Field(default_factory=list, max_length=30)
    risks: list[ArchitectureRisk] = Field(default_factory=list, max_length=30)
    assumptions: list[ArchitectureAssumption] = Field(default_factory=list, max_length=30)
    alternatives: list[ArchitectureAlternative] = Field(default_factory=list, max_length=8)
    cost_considerations: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[ArchitectureEvidence] = Field(default_factory=list, max_length=30)
    diagram: ArchitectureDiagram
    validation: ArchitectureValidation
    generated_at: datetime
    account_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _references_are_valid(self) -> ArchitectureProposal:
        component_ids = [item.component_id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component IDs must be unique")
        component_set = set(component_ids)
        for connection in self.connections:
            if connection.source_component_id not in component_set or connection.target_component_id not in component_set:
                raise ValueError("connection references an unknown component")
        for flow in self.data_flows:
            if flow.source_component_id not in component_set or flow.target_component_id not in component_set:
                raise ValueError("data flow references an unknown component")
        for component in self.components:
            if any(dependency not in component_set for dependency in component.dependencies):
                raise ValueError("component dependency references an unknown component")
        return self


class ArchitectureGenerationRequest(ArchitectureBase):
    request_text: str | None = Field(default=None, min_length=1, max_length=8_000)
    requirements: ArchitectureRequirements | None = None
    include_live_evidence: bool = False
    account_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _has_input(self) -> ArchitectureGenerationRequest:
        if self.request_text is None and self.requirements is None:
            raise ValueError("request_text or requirements is required")
        if self.account_id is not None and self.environment_id is not None:
            raise ValueError("account_id and environment_id are mutually exclusive")
        return self


class ArchitectureRevisionRequest(ArchitectureBase):
    request_text: str | None = Field(default=None, min_length=1, max_length=8_000)
    requirements: ArchitectureRequirements | None = None
    include_live_evidence: bool = False


class ArchitectureComparisonRequest(ArchitectureBase):
    proposal: ArchitectureProposal
    alternative_ids: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureComparisonResponse(ArchitectureBase):
    primary_proposal_id: uuid.UUID
    compared_alternatives: list[ArchitectureAlternative]
    criteria: list[str]
    missing_information: list[str]
    generated_at: datetime


class ArchitectureGraphResponse(ArchitectureBase):
    proposal_id: uuid.UUID
    revision: int
    diagram: ArchitectureDiagram
    components: list[ArchitectureComponent]
    connections: list[ArchitectureConnection]


class ArchitectureProposalPage(ArchitectureBase):
    items: list[ArchitectureProposal]
    has_more: bool
