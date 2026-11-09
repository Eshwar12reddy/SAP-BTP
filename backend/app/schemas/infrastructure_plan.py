"""Typed contracts for safe, deterministic SAP BTP infrastructure plans.

These contracts describe review artifacts only.  They intentionally contain no
deployment command, provider credential, or arbitrary template field.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InfrastructureBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


OutputFormat = Literal["cloudfoundry", "mta", "terraform"]
ValidationStatus = Literal["VALID", "PARTIAL", "INVALID", "UNSUPPORTED", "INCOMPLETE"]


class InfrastructureApplicationInput(InfrastructureBase):
    name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z][A-Za-z0-9._-]{0,159}$")
    memory: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]+(?:\.[0-9]+)?(?:M|MB|G|GB|MiB|GiB)$")
    instances: int | None = Field(default=None, ge=0, le=1_000_000)
    disk_quota: str | None = Field(default=None, max_length=20, pattern=r"^[0-9]+(?:\.[0-9]+)?(?:M|MB|G|GB|MiB|GiB)$")
    path: str | None = Field(default=None, min_length=1, max_length=500)
    command: str | None = Field(default=None, min_length=1, max_length=500)
    buildpacks: list[str] = Field(default_factory=list, max_length=4)
    stack: str | None = Field(default=None, max_length=80)
    routes: list[str] = Field(default_factory=list, max_length=10)
    service_bindings: list[str] = Field(default_factory=list, max_length=20)
    environment_variables: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _safe_inputs(self) -> InfrastructureApplicationInput:
        if any(not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", item) for item in self.environment_variables):
            raise ValueError("environment variable names must be safe identifiers")
        if any("${" in item or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,250}$", item) for item in self.routes):
            raise ValueError("route configuration is invalid")
        if self.path and (self.path.startswith(("/", "\\")) or ".." in self.path.replace("\\", "/").split("/")):
            raise ValueError("application path traversal is not allowed")
        return self


class InfrastructureTerraformSubaccount(InfrastructureBase):
    name: str = Field(min_length=1, max_length=160)
    subdomain: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$|^[a-z0-9]$")
    region: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class InfrastructureTerraformEntitlement(InfrastructureBase):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    subaccount_id: str = Field(min_length=1, max_length=200)
    service_name: str = Field(min_length=1, max_length=160)
    plan_name: str = Field(min_length=1, max_length=160)
    amount: int | None = Field(default=None, ge=0, le=1_000_000)


class InfrastructureTerraformServiceInstance(InfrastructureBase):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]{0,79}$")
    subaccount_id: str = Field(min_length=1, max_length=200)
    instance_name: str = Field(min_length=1, max_length=160)
    service_offering_name: str | None = Field(default=None, max_length=160)
    serviceplan_name: str | None = Field(default=None, max_length=160)
    serviceplan_id: str | None = Field(default=None, max_length=200)
    shared: bool | None = None

    @model_validator(mode="after")
    def _plan_reference(self) -> InfrastructureTerraformServiceInstance:
        if self.serviceplan_id is None and (self.service_offering_name is None or self.serviceplan_name is None):
            raise ValueError("serviceplan_id or service_offering_name and serviceplan_name are required")
        if self.serviceplan_id is not None and (self.service_offering_name is not None or self.serviceplan_name is not None):
            raise ValueError("serviceplan_id cannot be combined with name-based service plan fields")
        return self


class InfrastructureMtaModule(InfrastructureBase):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    module_type: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    path: str | None = Field(default=None, min_length=1, max_length=500)
    requires: list[str] = Field(default_factory=list, max_length=30)
    provides: list[str] = Field(default_factory=list, max_length=30)
    properties: dict[str, str] = Field(default_factory=dict, max_length=30)


class InfrastructureMtaResource(InfrastructureBase):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    resource_type: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
    service: str | None = Field(default=None, max_length=160)
    service_plan: str | None = Field(default=None, max_length=160)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=30)


class InfrastructurePlanRequirements(InfrastructureBase):
    architecture_proposal_id: uuid.UUID
    architecture_proposal_revision: int | None = Field(default=None, ge=1)
    sizing_result_id: uuid.UUID | None = None
    target_environment_id: uuid.UUID | None = None
    output_format: OutputFormat
    plan_id: uuid.UUID | None = None
    application_name: str | None = Field(default=None, max_length=160)
    applications: list[InfrastructureApplicationInput] = Field(default_factory=list, max_length=50)
    mta_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
    mta_version: str = Field(default="1.0.0", max_length=40, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    mta_modules: list[InfrastructureMtaModule] = Field(default_factory=list, max_length=50)
    mta_resources: list[InfrastructureMtaResource] = Field(default_factory=list, max_length=50)
    terraform_provider_version: str = Field(default="1.26.0", max_length=20, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    terraform_subaccounts: list[InfrastructureTerraformSubaccount] = Field(default_factory=list, max_length=20)
    terraform_entitlements: list[InfrastructureTerraformEntitlement] = Field(default_factory=list, max_length=50)
    terraform_service_instances: list[InfrastructureTerraformServiceInstance] = Field(default_factory=list, max_length=50)
    persist: bool = True

    @model_validator(mode="after")
    def _format_inputs(self) -> InfrastructurePlanRequirements:
        identifiers = [item.name for item in self.mta_modules] + [item.name for item in self.mta_resources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("MTA module and resource identifiers must be unique")
        if self.output_format == "cloudfoundry" and not self.applications and self.application_name is None:
            raise ValueError("application_name or applications is required for a Cloud Foundry manifest")
        if self.output_format == "terraform" and not (self.terraform_subaccounts or self.terraform_entitlements or self.terraform_service_instances):
            raise ValueError("at least one supported Terraform resource input is required")
        if self.target_environment_id is not None and self.output_format == "terraform":
            raise ValueError("Terraform account resources cannot target a runtime environment")
        return self


class InfrastructurePlanRequirementsAnalysis(InfrastructureBase):
    requirements: InfrastructurePlanRequirements
    required_inputs: list[str] = Field(default_factory=list, max_length=40)
    missing_prerequisites: list[str] = Field(default_factory=list, max_length=40)
    supported: bool
    analysis_status: Literal["READY", "NEEDS_INPUT", "UNSUPPORTED"]


class InfrastructureResource(InfrastructureBase):
    resource_id: str = Field(min_length=1, max_length=160)
    source_component_id: str | None = None
    resource_type: str = Field(min_length=1, max_length=160)
    target_runtime: str = Field(min_length=1, max_length=80)
    proposed: bool = True
    observed: bool = False
    configuration: dict[str, str | int | bool | list[str] | None] = Field(default_factory=dict, max_length=40)
    evidence_references: list[str] = Field(default_factory=list, max_length=20)


class InfrastructureDependency(InfrastructureBase):
    dependency_id: str = Field(min_length=1, max_length=160)
    source_resource_id: str
    target_resource_id: str
    relationship: str = Field(min_length=1, max_length=200)
    required: bool = True
    evidence_references: list[str] = Field(default_factory=list, max_length=20)


class InfrastructureParameter(InfrastructureBase):
    name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str
    sensitive: bool = False
    provenance: Literal["USER_PROVIDED", "DERIVED", "PLACEHOLDER", "UNKNOWN"]


class InfrastructureValidation(InfrastructureBase):
    rule_id: str
    severity: Literal["INFO", "WARNING", "ERROR"]
    message: str
    related_ids: list[str] = Field(default_factory=list, max_length=20)


class InfrastructureArtifact(InfrastructureBase):
    filename: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    media_type: str
    content: str = Field(max_length=500_000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=500_000)
    validation_status: ValidationStatus


class InfrastructureEvidence(InfrastructureBase):
    reference: str = Field(min_length=1, max_length=500)
    source_type: Literal["ARCHITECTURE", "SIZING", "DOCUMENTATION", "LIVE_PROVIDER", "CALCULATION", "USER_PROVIDED"]
    verification_status: Literal["VERIFIED", "PARTIAL", "NOT_VERIFIED"]
    supports: list[str] = Field(default_factory=list, max_length=20)


class InfrastructureImpactSummary(InfrastructureBase):
    proposed_resources: list[str] = Field(default_factory=list, max_length=100)
    existing_resources_referenced: list[str] = Field(default_factory=list, max_length=100)
    potentially_required_resources: list[str] = Field(default_factory=list, max_length=100)
    configuration_changes: list[str] = Field(default_factory=list, max_length=100)
    associated_environments: list[str] = Field(default_factory=list, max_length=20)
    quota_impact: list[str] = Field(default_factory=list, max_length=40)
    prerequisites: list[str] = Field(default_factory=list, max_length=40)
    estimated_capacity: list[str] = Field(default_factory=list, max_length=40)
    risks: list[str] = Field(default_factory=list, max_length=40)
    missing_information: list[str] = Field(default_factory=list, max_length=40)
    unsupported_operations: list[str] = Field(default_factory=lambda: ["No deployment, apply, push, build, or provisioning operation is performed."])


class InfrastructurePlan(InfrastructureBase):
    plan_id: uuid.UUID
    owner_user_id: uuid.UUID
    revision: int = Field(ge=1)
    architecture_proposal_id: uuid.UUID
    architecture_proposal_revision: int
    sizing_result_id: uuid.UUID | None
    sizing_formula_version: str | None
    target_runtime: str
    target_environment_id: uuid.UUID | None
    output_format: OutputFormat
    resources: list[InfrastructureResource] = Field(default_factory=list, max_length=200)
    dependencies: list[InfrastructureDependency] = Field(default_factory=list, max_length=300)
    parameters: list[InfrastructureParameter] = Field(default_factory=list, max_length=100)
    input_provenance: dict[str, str] = Field(default_factory=dict, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    missing_prerequisites: list[str] = Field(default_factory=list, max_length=100)
    unsupported_components: list[str] = Field(default_factory=list, max_length=100)
    validations: list[InfrastructureValidation] = Field(default_factory=list, max_length=200)
    validation_status: ValidationStatus
    generation_timestamp: datetime
    generator_version: str
    evidence: list[InfrastructureEvidence] = Field(default_factory=list, max_length=100)
    artifacts: list[InfrastructureArtifact] = Field(default_factory=list, max_length=20)
    impact: InfrastructureImpactSummary
    review_report: str = Field(max_length=100_000)


class InfrastructurePlanRevision(InfrastructureBase):
    plan_id: uuid.UUID
    revision: int
    created_at: datetime
    validation_status: ValidationStatus
    output_format: OutputFormat
    architecture_proposal_revision: int
    sizing_result_id: uuid.UUID | None


class InfrastructurePlanPage(InfrastructureBase):
    items: list[InfrastructurePlan]
    has_more: bool


class InfrastructureRevisionPage(InfrastructureBase):
    items: list[InfrastructurePlanRevision]
    has_more: bool = False


class InfrastructurePlanCompareResponse(InfrastructureBase):
    plan_id: uuid.UUID
    left_revision: int
    right_revision: int
    changed_files: list[str]
    added_resources: list[str]
    removed_resources: list[str]
    changed_fields: list[str]
    generated_at: datetime


class InfrastructureArtifactResponse(InfrastructureBase):
    plan_id: uuid.UUID
    revision: int
    artifact: InfrastructureArtifact
