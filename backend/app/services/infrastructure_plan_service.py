"""Deterministic, review-only infrastructure configuration generation.

The service is deliberately a serializer over typed inputs.  It never invokes cf,
mbt, terraform, a provider API, a shell, or an AI model.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, coverage_for
from app.auth.permissions import Permission
from app.core.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.models import ArchitectureProposalRecord, InfrastructurePlanRecord, SizingResultRecord
from app.schemas.architecture import ArchitectureProposal
from app.schemas.infrastructure_plan import (
    InfrastructureApplicationInput,
    InfrastructureArtifact,
    InfrastructureArtifactResponse,
    InfrastructureDependency,
    InfrastructureEvidence,
    InfrastructureImpactSummary,
    InfrastructureParameter,
    InfrastructurePlan,
    InfrastructurePlanCompareResponse,
    InfrastructurePlanRequirements,
    InfrastructurePlanRequirementsAnalysis,
    InfrastructurePlanRevision,
    InfrastructureResource,
    InfrastructureRevisionPage,
    InfrastructureValidation,
)
from app.schemas.sizing import SizingResult

if TYPE_CHECKING:
    from app.core.config import Settings

GENERATOR_VERSION = "phase24-v1"
CF_DOC = "https://docs.cloudfoundry.org/devguide/deploy-apps/manifest-attributes.html"
MTA_DOC = "https://help.sap.com/docs/btp/sap-business-technology-platform/multitarget-applications-in-cloud-foundry-environment"
TERRAFORM_DOC = "https://registry.terraform.io/providers/SAP/btp/latest/docs"
_SAFE_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_ROUTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,250}$")
_SAFE_HCL_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,79}$")
_ALLOWED_CF_APP = {"name", "memory", "instances", "disk_quota", "path", "command", "buildpacks", "stack", "routes", "services", "env"}
_ALLOWED_TF_RESOURCES = {
    "btp_subaccount": {"name", "subdomain", "region", "parent_id", "description"},
    "btp_subaccount_entitlement": {"subaccount_id", "service_name", "plan_name", "amount"},
    "btp_subaccount_service_instance": {"subaccount_id", "name", "service_offering_name", "serviceplan_name", "serviceplan_id", "shared"},
}


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _yaml(value: Any) -> str:
    try:
        import yaml
    except ImportError:  # pragma: no cover - dependency is pinned, fallback keeps imports usable
        raise BadRequestError("YAML validation tooling is unavailable.", code="yaml_validator_unavailable") from None
    return yaml.safe_dump(value, sort_keys=False, default_flow_style=False, allow_unicode=False)


def _yaml_validate(content: str) -> tuple[bool, str]:
    try:
        import yaml
        parsed = yaml.safe_load(content)
    except ImportError:
        return False, "YAML validation tooling is unavailable."
    except yaml.YAMLError:
        return False, "The generated YAML does not parse."
    return isinstance(parsed, dict), "YAML syntax parsed successfully." if isinstance(parsed, dict) else "YAML document must be an object."


def _quote_hcl(value: str) -> str:
    if any(token in value for token in ("${", "$(", "`", "\r", "\n")):
        raise BadRequestError("Terraform input contains unsafe interpolation characters.", code="hcl_injection_rejected")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hcl_attr(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return f"  {key} = {'true' if value else 'false'}"
    if isinstance(value, int):
        return f"  {key} = {value}"
    return f"  {key} = {_quote_hcl(str(value))}"


def _size_string(value: Any, unit: str = "MB") -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.removesuffix(".0")
    return f"{text}{unit}"


class InfrastructurePlanService:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings

    async def analyze(self, session: AsyncSession, principal: Principal, requirements: InfrastructurePlanRequirements) -> InfrastructurePlanRequirementsAnalysis:
        required: list[str] = []
        missing: list[str] = []
        supported = True
        record = await self._proposal_record(session, principal, requirements.architecture_proposal_id)
        await self._ensure_environment_access(session, principal, requirements.target_environment_id)
        proposal = ArchitectureProposal.model_validate(record.proposal_json)
        if requirements.architecture_proposal_revision is not None and requirements.architecture_proposal_revision != proposal.revision:
            missing.append(f"Architecture revision {requirements.architecture_proposal_revision} is not the latest authorized revision ({proposal.revision}).")
        if requirements.output_format in {"cloudfoundry", "mta"} and requirements.target_environment_id is not None and proposal.environment_id not in {None, requirements.target_environment_id}:
            missing.append("Target environment must match the architecture proposal environment.")
        if requirements.output_format == "mta" and not requirements.mta_modules:
            missing.append("Explicit MTA module type and path inputs are required for deployment-ready modules.")
        if requirements.output_format == "terraform" and not proposal.account_id and not requirements.terraform_subaccounts:
            missing.append("An explicit subaccount input is required; architecture metadata alone is not a provider resource definition.")
        if requirements.sizing_result_id is not None:
            sizing = await self._sizing_record(session, principal, requirements.sizing_result_id)
            result = SizingResult.model_validate(sizing.result_json)
            if result.architecture_proposal_id != requirements.architecture_proposal_id:
                missing.append("Sizing result does not belong to the selected architecture proposal.")
        for app in requirements.applications:
            self._validate_app_inputs(app)
        if requirements.output_format not in {"cloudfoundry", "mta", "terraform"}:
            supported = False
        status = "UNSUPPORTED" if not supported else "NEEDS_INPUT" if missing else "READY"
        return InfrastructurePlanRequirementsAnalysis(requirements=requirements, required_inputs=required, missing_prerequisites=_dedupe(missing), supported=supported, analysis_status=status)

    async def generate(self, session: AsyncSession, principal: Principal, requirements: InfrastructurePlanRequirements) -> InfrastructurePlan:
        record = await self._proposal_record(session, principal, requirements.architecture_proposal_id)
        await self._ensure_environment_access(session, principal, requirements.target_environment_id)
        proposal = ArchitectureProposal.model_validate(record.proposal_json)
        if requirements.target_environment_id is not None and proposal.environment_id not in {None, requirements.target_environment_id}:
            raise PermissionDeniedError("The target environment does not match the architecture proposal.", code="infrastructure_environment_mismatch")
        sizing: SizingResult | None = None
        warnings: list[str] = []
        missing: list[str] = []
        unsupported: list[str] = []
        if requirements.sizing_result_id is not None:
            sizing = SizingResult.model_validate((await self._sizing_record(session, principal, requirements.sizing_result_id)).result_json)
            if sizing.architecture_proposal_id not in {None, requirements.architecture_proposal_id}:
                raise BadRequestError("The sizing result is not associated with the selected architecture proposal.", code="infrastructure_sizing_proposal_mismatch")
            warnings.extend(sizing.warnings)
            missing.extend(sizing.missing_inputs)
        if requirements.architecture_proposal_revision is not None and requirements.architecture_proposal_revision != proposal.revision:
            warnings.append(f"Architecture revision mismatch: requested {requirements.architecture_proposal_revision}, latest authorized revision is {proposal.revision}.")
        if sizing and sizing.architecture_proposal_revision is not None and sizing.architecture_proposal_revision != proposal.revision:
            warnings.append("Sizing result belongs to an older architecture revision; updated sizing or explicit review is required.")
        target_runtime = self._runtime(proposal)
        if requirements.output_format in {"cloudfoundry", "mta"} and target_runtime not in {"cloudfoundry", "cloudfoundry_or_kyma"}:
            unsupported.append(f"Architecture runtime {target_runtime} has no verified {requirements.output_format} mapping.")
        resources, dependencies, parameters, artifacts, format_validations = self._render(requirements, proposal, sizing, warnings, missing, unsupported)
        validations = list(format_validations)
        validations.extend(self._validate_artifacts(requirements.output_format, artifacts))
        validations.extend(InfrastructureValidation(rule_id="generation-warning", severity="WARNING", message=item, related_ids=[]) for item in _dedupe(warnings))
        if missing:
            validations.append(InfrastructureValidation(rule_id="missing-prerequisites", severity="WARNING", message="Required inputs or prerequisites are unresolved.", related_ids=[]))
        for item in unsupported:
            validations.append(InfrastructureValidation(rule_id="unsupported-component", severity="WARNING", message=item, related_ids=[]))
        has_error = any(item.severity == "ERROR" for item in validations)
        status = "INVALID" if has_error else "INCOMPLETE" if missing or unsupported else "VALID"
        if any(item.severity == "WARNING" for item in validations) and status == "VALID":
            status = "PARTIAL"
        all_evidence = [InfrastructureEvidence(reference=CF_DOC if requirements.output_format == "cloudfoundry" else MTA_DOC if requirements.output_format == "mta" else TERRAFORM_DOC, source_type="DOCUMENTATION", verification_status="VERIFIED", supports=[requirements.output_format])]
        all_evidence.extend(InfrastructureEvidence(reference=item, source_type="ARCHITECTURE", verification_status="VERIFIED", supports=["architecture component"] ) for item in _dedupe([ref for component in proposal.components for ref in component.evidence_references]))
        if sizing:
            all_evidence.append(InfrastructureEvidence(reference=str(sizing.result_id), source_type="SIZING", verification_status="VERIFIED", supports=["capacity values", "quota impact"]))
        impact = self._impact(proposal, sizing, resources, missing, unsupported, warnings)
        plan_id = requirements.plan_id or uuid.uuid4()
        revision = await self._next_revision(session, principal, plan_id)
        plan = InfrastructurePlan(plan_id=plan_id, owner_user_id=principal.user_id, revision=revision, architecture_proposal_id=proposal.proposal_id, architecture_proposal_revision=proposal.revision, sizing_result_id=sizing.result_id if sizing else None, sizing_formula_version=sizing.formula_version if sizing else None, target_runtime=target_runtime, target_environment_id=requirements.target_environment_id or proposal.environment_id, output_format=requirements.output_format, resources=resources, dependencies=dependencies, parameters=parameters, input_provenance={"architecture_proposal": "AUTHORIZED_OWNER_SCOPED_RECORD", "sizing_result": "AUTHORIZED_OWNER_SCOPED_RECORD" if sizing else "NOT_PROVIDED", "configuration_parameters": "USER_PROVIDED_OR_PLACEHOLDER"}, assumptions=_dedupe(warnings + ["Generated content is a static review artifact and is never executed."]), missing_prerequisites=_dedupe(missing), unsupported_components=_dedupe(unsupported), validations=validations, validation_status=status, generation_timestamp=datetime.now(UTC), generator_version=GENERATOR_VERSION, evidence=all_evidence, artifacts=artifacts, impact=impact, review_report=self._review_report(plan_id, revision, proposal, sizing, status, artifacts, warnings, missing, unsupported))
        if requirements.persist:
            session.add(InfrastructurePlanRecord(plan_id=plan.plan_id, revision=plan.revision, owner_user_id=principal.user_id, btp_account_id=record.btp_account_id, btp_environment_id=plan.target_environment_id, architecture_proposal_id=plan.architecture_proposal_id, architecture_proposal_revision=plan.architecture_proposal_revision, sizing_result_id=plan.sizing_result_id, output_format=plan.output_format, plan_json=plan.model_dump(mode="json"), requirements_json=requirements.model_dump(mode="json")))
            await session.commit()
        return plan

    def validate(self, plan: InfrastructurePlan) -> InfrastructurePlan:
        checks = self._validate_artifacts(plan.output_format, plan.artifacts)
        current = [item for item in plan.validations if item.rule_id not in {"static-validation", "yaml-syntax", "mta-references", "hcl-syntax"}]
        current.extend(checks)
        has_error = any(item.severity == "ERROR" for item in current)
        status = "INVALID" if has_error else "PARTIAL" if any(item.severity == "WARNING" for item in current) else "VALID"
        return plan.model_copy(update={"validations": current, "validation_status": status})

    async def get(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID, revision: int | None = None) -> InfrastructurePlan:
        query = select(InfrastructurePlanRecord).where(InfrastructurePlanRecord.plan_id == plan_id, InfrastructurePlanRecord.owner_user_id == principal.user_id)
        if revision is not None:
            query = query.where(InfrastructurePlanRecord.revision == revision)
        query = query.order_by(InfrastructurePlanRecord.revision.desc())
        record = await session.scalar(query)
        if record is None:
            raise NotFoundError("Infrastructure plan not found.")
        return InfrastructurePlan.model_validate(record.plan_json)

    async def revisions(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID) -> InfrastructureRevisionPage:
        rows = list(await session.scalars(select(InfrastructurePlanRecord).where(InfrastructurePlanRecord.plan_id == plan_id, InfrastructurePlanRecord.owner_user_id == principal.user_id).order_by(InfrastructurePlanRecord.revision.desc())))
        if not rows:
            raise NotFoundError("Infrastructure plan not found.")
        return InfrastructureRevisionPage(items=[InfrastructurePlanRevision(plan_id=row.plan_id, revision=row.revision, created_at=row.created_at, validation_status=InfrastructurePlan.model_validate(row.plan_json).validation_status, output_format=row.output_format, architecture_proposal_revision=row.architecture_proposal_revision, sizing_result_id=row.sizing_result_id) for row in rows])

    async def compare(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID, left_revision: int, right_revision: int) -> InfrastructurePlanCompareResponse:
        left = await self.get(session, principal, plan_id, left_revision)
        right = await self.get(session, principal, plan_id, right_revision)
        left_files = {item.filename: item.content for item in left.artifacts}
        right_files = {item.filename: item.content for item in right.artifacts}
        changed = sorted(name for name in left_files.keys() & right_files.keys() if left_files[name] != right_files[name])
        added = sorted(item.resource_id for item in right.resources if item.resource_id not in {r.resource_id for r in left.resources})
        removed = sorted(item.resource_id for item in left.resources if item.resource_id not in {r.resource_id for r in right.resources})
        fields = []
        if left.architecture_proposal_revision != right.architecture_proposal_revision:
            fields.append("architecture_proposal_revision")
        if left.sizing_result_id != right.sizing_result_id:
            fields.append("sizing_result_id")
        return InfrastructurePlanCompareResponse(plan_id=plan_id, left_revision=left_revision, right_revision=right_revision, changed_files=changed, added_resources=added, removed_resources=removed, changed_fields=fields, generated_at=datetime.now(UTC))

    async def artifact(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID, filename: str, revision: int | None = None) -> InfrastructureArtifactResponse:
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise BadRequestError("Unsafe artifact filename.", code="unsafe_filename")
        plan = await self.get(session, principal, plan_id, revision)
        item = next((artifact for artifact in plan.artifacts if artifact.filename == filename), None)
        if item is None:
            raise NotFoundError("Artifact not found.")
        return InfrastructureArtifactResponse(plan_id=plan.plan_id, revision=plan.revision, artifact=item)

    async def export_zip(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID, revision: int | None = None) -> bytes:
        plan = await self.get(session, principal, plan_id, revision)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for artifact in plan.artifacts:
                archive.writestr(artifact.filename, artifact.content)
            archive.writestr("review-report.md", plan.review_report)
        return buffer.getvalue()

    async def _proposal_record(self, session: AsyncSession, principal: Principal, proposal_id: uuid.UUID) -> ArchitectureProposalRecord:
        record = await session.scalar(select(ArchitectureProposalRecord).where(ArchitectureProposalRecord.proposal_id == proposal_id, ArchitectureProposalRecord.owner_user_id == principal.user_id).order_by(ArchitectureProposalRecord.revision.desc()))
        if record is None:
            raise NotFoundError("Architecture proposal not found.")
        coverage = await coverage_for(session, principal, Permission.BTP_READ)
        if record.btp_account_id is not None and not coverage.covers_account(record.btp_account_id):
            raise PermissionDeniedError("The architecture proposal is outside your authorized BTP scope.", code="btp_scope_revoked")
        if record.btp_environment_id is not None and not coverage.covers_environment(record.btp_environment_id):
            raise PermissionDeniedError("The architecture proposal is outside your authorized BTP scope.", code="btp_scope_revoked")
        return record

    @staticmethod
    async def _ensure_environment_access(session: AsyncSession, principal: Principal, environment_id: uuid.UUID | None) -> None:
        if environment_id is None:
            return
        coverage = await coverage_for(session, principal, Permission.BTP_READ)
        if not coverage.covers_environment(environment_id):
            raise PermissionDeniedError("The target environment is outside your authorized BTP scope.", code="btp_scope_revoked")

    async def _sizing_record(self, session: AsyncSession, principal: Principal, result_id: uuid.UUID) -> SizingResultRecord:
        record = await session.scalar(select(SizingResultRecord).where(SizingResultRecord.result_id == result_id, SizingResultRecord.owner_user_id == principal.user_id))
        if record is None:
            raise NotFoundError("Sizing result not found.")
        return record

    async def _next_revision(self, session: AsyncSession, principal: Principal, plan_id: uuid.UUID) -> int:
        latest = await session.scalar(select(InfrastructurePlanRecord.revision).where(InfrastructurePlanRecord.plan_id == plan_id, InfrastructurePlanRecord.owner_user_id == principal.user_id).order_by(InfrastructurePlanRecord.revision.desc()))
        return (latest or 0) + 1

    @staticmethod
    def _runtime(proposal: ArchitectureProposal) -> str:
        selected = next((item.runtime_id for item in proposal.runtime_options if item.suitability == "SELECTED"), None)
        return selected or (proposal.runtime_options[0].runtime_id if proposal.runtime_options else "unknown")

    @staticmethod
    def _validate_app_inputs(app: InfrastructureApplicationInput) -> None:
        for variable in app.environment_variables:
            if not _SAFE_ENV.match(variable):
                raise BadRequestError("Environment variable names must be safe identifiers.", code="unsafe_environment_name")
        for route in app.routes:
            if not _SAFE_ROUTE.match(route) or "${" in route:
                raise BadRequestError("Route configuration is invalid.", code="invalid_route")
        if app.path and (".." in app.path.replace("\\", "/").split("/") or app.path.startswith("/")):
            raise BadRequestError("Application paths cannot traverse outside the project.", code="path_traversal_rejected")

    def _render(self, requirements: InfrastructurePlanRequirements, proposal: ArchitectureProposal, sizing: SizingResult | None, warnings: list[str], missing: list[str], unsupported: list[str]) -> tuple[list[InfrastructureResource], list[InfrastructureDependency], list[InfrastructureParameter], list[InfrastructureArtifact], list[InfrastructureValidation]]:
        if requirements.output_format == "cloudfoundry":
            return self._render_cf(requirements, proposal, sizing, warnings, missing, unsupported)
        if requirements.output_format == "mta":
            return self._render_mta(requirements, proposal, sizing, warnings, missing, unsupported)
        return self._render_tf(requirements, proposal, warnings, missing, unsupported)

    def _sizing_values(self, sizing: SizingResult | None) -> tuple[str | None, int | None, str | None]:
        memory = instances = disk = None
        if sizing:
            for item in sizing.calculations:
                if item.calculation_type in {"CONFIGURED_MEMORY", "OBSERVED_MEMORY"} and memory is None:
                    memory = _size_string(item.final_value, "MB")
                elif item.calculation_type == "INSTANCE_REQUIREMENT" and item.final_value is not None:
                    instances = int(item.final_value)
                elif item.calculation_type == "CONFIGURED_DISK" and disk is None:
                    disk = _size_string(item.final_value, "MB")
        return memory, instances, disk

    def _render_cf(self, requirements: InfrastructurePlanRequirements, proposal: ArchitectureProposal, sizing: SizingResult | None, warnings: list[str], missing: list[str], unsupported: list[str]):
        memory, instances, disk = self._sizing_values(sizing)
        apps = requirements.applications or [InfrastructureApplicationInput(name=requirements.application_name or "app")]
        rendered: list[dict[str, Any]] = []
        resources: list[InfrastructureResource] = []
        parameters: list[InfrastructureParameter] = []
        for index, app in enumerate(apps):
            self._validate_app_inputs(app)
            item: dict[str, Any] = {"name": app.name}
            if app.memory or memory: item["memory"] = app.memory or memory
            if app.instances is not None or instances is not None: item["instances"] = app.instances if app.instances is not None else instances
            if app.disk_quota or disk: item["disk_quota"] = app.disk_quota or disk
            for key, value in (("path", app.path), ("command", app.command), ("stack", app.stack)):
                if value is not None: item[key] = value
            if app.buildpacks: item["buildpacks"] = list(app.buildpacks)
            if app.routes: item["routes"] = [{"route": route} for route in app.routes]
            if app.service_bindings:
                item["services"] = [{"name": name, "binding_name": name} for name in app.service_bindings]
            if app.environment_variables:
                item["env"] = {name: f"(({name}))" for name in app.environment_variables}
                parameters.extend(InfrastructureParameter(name=name, value=f"(({name}))", sensitive=True, provenance="PLACEHOLDER") for name in app.environment_variables)
            rendered.append(item)
            component = next((c for c in proposal.components if c.component_type == "APPLICATION" and (c.name == app.name or index == 0)), None)
            resources.append(InfrastructureResource(resource_id=app.name, source_component_id=component.component_id if component else None, resource_type="cloudfoundry_application_manifest", target_runtime="cloudfoundry", configuration={k: v for k, v in item.items() if isinstance(v, (str, int, bool)) or v is None}, evidence_references=component.evidence_references if component else []))
        yaml_data = {"version": 1, "applications": rendered}
        content = "---\n" + _yaml(yaml_data)
        artifact = self._artifact("manifest.yml", "application/x-yaml", content, "VALID")
        for component in proposal.components:
            if component.component_type not in {"APPLICATION", "SERVICE", "DATA_STORE", "ENVIRONMENT", "ACTOR", "EXTERNAL_SYSTEM", "SAP_SYSTEM"}:
                unsupported.append(f"Component {component.component_id} has no verified Cloud Foundry mapping.")
        missing.extend(["Application path/buildpack/start command" ] if not any(app.path or app.buildpacks or app.command for app in apps) else [])
        warnings.append("Referenced services are binding names only; Cloud Foundry manifests do not provision service instances.") if any(app.service_bindings for app in apps) else None
        return resources, [], parameters, [artifact], []

    def _render_mta(self, requirements: InfrastructurePlanRequirements, proposal: ArchitectureProposal, sizing: SizingResult | None, warnings: list[str], missing: list[str], unsupported: list[str]):
        modules = requirements.mta_modules
        resources_input = requirements.mta_resources
        if not modules:
            missing.append("MTA module type and path inputs")
        names = {item.name for item in modules} | {item.name for item in resources_input}
        if len(names) != len(modules) + len(resources_input):
            raise BadRequestError("MTA module and resource identifiers must be unique.", code="duplicate_mta_identifier")
        resources: list[InfrastructureResource] = []
        dependencies: list[InfrastructureDependency] = []
        module_yaml: list[dict[str, Any]] = []
        for item in modules:
            data: dict[str, Any] = {"name": item.name, "type": item.module_type}
            if item.path: data["path"] = item.path
            if item.requires: data["requires"] = [{"name": name} for name in item.requires]
            if item.provides: data["provides"] = [{"name": name} for name in item.provides]
            if item.properties: data["properties"] = dict(sorted(item.properties.items()))
            module_yaml.append(data)
            resources.append(InfrastructureResource(resource_id=item.name, source_component_id=next((c.component_id for c in proposal.components if c.name == item.name), None), resource_type="mta_module", target_runtime="cloudfoundry", configuration={"type": item.module_type, "path": item.path}, evidence_references=[]))
            for dependency in item.requires:
                dependencies.append(InfrastructureDependency(dependency_id=f"{item.name}-requires-{dependency}", source_resource_id=item.name, target_resource_id=dependency, relationship="requires", evidence_references=[]))
                if dependency not in names:
                    missing.append(f"MTA dependency {dependency} referenced by module {item.name} is not declared.")
        resource_yaml: list[dict[str, Any]] = []
        for item in resources_input:
            data: dict[str, Any] = {"name": item.name, "type": item.resource_type}
            if item.service: data["parameters"] = {"service": item.service}
            if item.service_plan: data.setdefault("parameters", {})["service-plan"] = item.service_plan
            if item.parameters: data.setdefault("parameters", {}).update(dict(sorted(item.parameters.items())))
            resource_yaml.append(data)
            resources.append(InfrastructureResource(resource_id=item.name, source_component_id=next((c.component_id for c in proposal.components if c.related_service_key == item.name), None), resource_type="mta_resource", target_runtime="cloudfoundry", configuration={"type": item.resource_type}, evidence_references=[]))
        data = {"_schema-version": "3.3", "ID": requirements.mta_id or proposal.title.lower().replace(" ", "-")[:80], "version": requirements.mta_version, "modules": module_yaml}
        if resource_yaml: data["resources"] = resource_yaml
        content = "---\n" + _yaml(data)
        artifact = self._artifact("mta.yaml", "application/x-yaml", content, "PARTIAL" if missing else "VALID")
        warnings.append("MTA deployment readiness is incomplete until all module build inputs and resource service/plan contracts are reviewed.") if missing else None
        return resources, dependencies, [], [artifact], []

    def _render_tf(self, requirements: InfrastructurePlanRequirements, proposal: ArchitectureProposal, warnings: list[str], missing: list[str], unsupported: list[str]):
        blocks: list[str] = ["terraform {", "  required_providers {", "    btp = {", "      source = \"SAP/btp\"", f"      version = \"{requirements.terraform_provider_version}\"", "    }", "  }", "}", "", "provider \"btp\" {}", ""]
        variables = ["variable \"subaccount_id\" {", "  type = string", "  description = \"Authorized SAP BTP subaccount ID; provide at review time.\"", "  sensitive = false", "}", ""]
        resources: list[InfrastructureResource] = []
        for item in requirements.terraform_subaccounts:
            if not _SAFE_HCL_ID.match(item.subdomain.replace("-", "_")):
                raise BadRequestError("Terraform identifier is invalid.", code="invalid_hcl_identifier")
            attrs = [("name", item.name), ("subdomain", item.subdomain), ("region", item.region)]
            if item.parent_id: attrs.append(("parent_id", item.parent_id))
            if item.description: attrs.append(("description", item.description))
            blocks.append(f'resource "btp_subaccount" "{item.subdomain.replace("-", "_")}" {{')
            blocks.extend(_hcl_attr(key, value) for key, value in attrs)
            blocks.append("}\n")
            resources.append(InfrastructureResource(resource_id=item.name, source_component_id=None, resource_type="btp_subaccount", target_runtime="btp_account", configuration=dict(attrs), evidence_references=[TERRAFORM_DOC]))
        for item in requirements.terraform_entitlements:
            blocks.append(f'resource "btp_subaccount_entitlement" "{item.name}" {{')
            blocks.extend(_hcl_attr(key, value) for key, value in (("subaccount_id", item.subaccount_id), ("service_name", item.service_name), ("plan_name", item.plan_name)))
            if item.amount is not None: blocks.append(_hcl_attr("amount", item.amount))
            blocks.append("}\n")
            resources.append(InfrastructureResource(resource_id=item.name, source_component_id=None, resource_type="btp_subaccount_entitlement", target_runtime="btp_account", configuration={"service_name": item.service_name, "plan_name": item.plan_name}, evidence_references=[TERRAFORM_DOC]))
        for item in requirements.terraform_service_instances:
            blocks.append(f'resource "btp_subaccount_service_instance" "{item.name}" {{')
            blocks.extend(_hcl_attr(key, value) for key, value in (("subaccount_id", item.subaccount_id), ("name", item.instance_name)))
            if item.serviceplan_id: blocks.append(_hcl_attr("serviceplan_id", item.serviceplan_id))
            else:
                blocks.extend(_hcl_attr(key, value) for key, value in (("service_offering_name", item.service_offering_name), ("serviceplan_name", item.serviceplan_name)) if value is not None)
            if item.shared is not None: blocks.append(_hcl_attr("shared", item.shared))
            blocks.append("}\n")
            resources.append(InfrastructureResource(resource_id=item.name, source_component_id=None, resource_type="btp_subaccount_service_instance", target_runtime="btp_account", configuration={"name": item.instance_name}, evidence_references=[TERRAFORM_DOC]))
        if not resources:
            unsupported.append("No supported SAP/btp resource was supplied.")
        warnings.append("Terraform provider schema and remote entitlement state require provider-side validation; no terraform command or provider download was run.")
        main = "\n".join(blocks)
        artifact = self._artifact("main.tf", "application/hcl", main, "PARTIAL")
        variables_artifact = self._artifact("variables.tf", "application/hcl", "\n".join(variables), "VALID")
        example = self._artifact("terraform.tfvars.example", "text/plain", "# Example only; no credentials or secrets are included.\nsubaccount_id = \"<AUTHORIZED_SUBACCOUNT_ID>\"\n", "VALID")
        return resources, [], [], [artifact, variables_artifact, example], []

    @staticmethod
    def _artifact(filename: str, media_type: str, content: str, status: str) -> InfrastructureArtifact:
        return InfrastructureArtifact(filename=filename, media_type=media_type, content=content, sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()), validation_status=status)

    def _validate_artifacts(self, output_format: str, artifacts: list[InfrastructureArtifact]) -> list[InfrastructureValidation]:
        results: list[InfrastructureValidation] = []
        expected = "manifest.yml" if output_format == "cloudfoundry" else "mta.yaml" if output_format == "mta" else "main.tf"
        if not any(item.filename == expected for item in artifacts):
            results.append(InfrastructureValidation(rule_id="artifact-required", severity="ERROR", message=f"{expected} is required.", related_ids=[]))
            return results
        primary = next(item for item in artifacts if item.filename == expected)
        if output_format in {"cloudfoundry", "mta"}:
            valid, message = _yaml_validate(primary.content)
            results.append(InfrastructureValidation(rule_id="yaml-syntax", severity="INFO" if valid else "ERROR", message=message, related_ids=[expected]))
            if valid:
                try:
                    import yaml
                    data = yaml.safe_load(primary.content)
                    if output_format == "cloudfoundry":
                        if set(data) - {"version", "applications"}: results.append(InfrastructureValidation(rule_id="cf-fields", severity="ERROR", message="Unsupported Cloud Foundry manifest top-level field.", related_ids=[expected]))
                        if not isinstance(data.get("applications"), list) or not data["applications"]: results.append(InfrastructureValidation(rule_id="cf-applications", severity="ERROR", message="Cloud Foundry applications must be a non-empty list.", related_ids=[expected]))
                        for app in data.get("applications", []):
                            if set(app) - _ALLOWED_CF_APP: results.append(InfrastructureValidation(rule_id="cf-fields", severity="ERROR", message="Unsupported Cloud Foundry application field.", related_ids=[expected]))
                    else:
                        if data.get("_schema-version") != "3.3": results.append(InfrastructureValidation(rule_id="mta-schema-version", severity="ERROR", message="MTA schema version must be 3.3 for this generator.", related_ids=[expected]))
                        if not isinstance(data.get("ID"), str) or not isinstance(data.get("version"), str): results.append(InfrastructureValidation(rule_id="mta-required-fields", severity="ERROR", message="MTA ID and version are required.", related_ids=[expected]))
                        if not isinstance(data.get("modules"), list): results.append(InfrastructureValidation(rule_id="mta-modules", severity="ERROR", message="MTA modules must be a list.", related_ids=[expected]))
                        names = [item.get("name") for item in data.get("modules", []) + data.get("resources", [])]
                        if len(names) != len(set(names)): results.append(InfrastructureValidation(rule_id="mta-identifiers", severity="ERROR", message="MTA module and resource identifiers must be unique.", related_ids=[expected]))
                        declared = set(names)
                        for item in data.get("modules", []):
                            for req in item.get("requires", []):
                                if req.get("name") not in declared: results.append(InfrastructureValidation(rule_id="mta-references", severity="ERROR", message="MTA module dependency is not declared.", related_ids=[expected]))
                except (AttributeError, KeyError, TypeError):
                    results.append(InfrastructureValidation(rule_id="schema-validation", severity="WARNING", message="Local YAML parser could not complete schema checks.", related_ids=[expected]))
        else:
            lines = primary.content.splitlines()
            for line in lines:
                match = re.match(r'^resource\s+"([^"]+)"\s+"([^"]+)"\s*\{', line.strip())
                if match:
                    resource_type, identifier = match.groups()
                    if resource_type not in _ALLOWED_TF_RESOURCES: results.append(InfrastructureValidation(rule_id="terraform-resource-type", severity="ERROR", message=f"Unsupported Terraform resource type: {resource_type}.", related_ids=[expected]))
                    if not _SAFE_HCL_ID.match(identifier): results.append(InfrastructureValidation(rule_id="hcl-identifier", severity="ERROR", message="Invalid Terraform resource identifier.", related_ids=[expected]))
                if any(token in line for token in ("${", "$(`", "terraform apply", "terraform plan", "terraform destroy")):
                    results.append(InfrastructureValidation(rule_id="hcl-safety", severity="ERROR", message="Unsafe Terraform interpolation or operation text detected.", related_ids=[expected]))
            for match in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{(.*?)\n\}', primary.content, re.DOTALL):
                resource_type, identifier, body = match.groups()
                attributes = set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", body, re.MULTILINE))
                required = {"btp_subaccount": {"name", "subdomain", "region"}, "btp_subaccount_entitlement": {"subaccount_id", "service_name", "plan_name"}, "btp_subaccount_service_instance": {"subaccount_id", "name"}}.get(resource_type, set())
                missing_attributes = required - attributes
                if resource_type == "btp_subaccount_service_instance" and "serviceplan_id" not in attributes and not {"service_offering_name", "serviceplan_name"} <= attributes:
                    missing_attributes.add("serviceplan_id or service_offering_name/serviceplan_name")
                if missing_attributes:
                    results.append(InfrastructureValidation(rule_id="terraform-required-attributes", severity="ERROR", message=f"Terraform resource {resource_type}.{identifier} is missing required attributes.", related_ids=[expected]))
            results.append(InfrastructureValidation(rule_id="hcl-syntax", severity="INFO", message="HCL structure passed local static checks; provider schema and remote state were not checked.", related_ids=[expected]))
        return results

    @staticmethod
    def _impact(proposal: ArchitectureProposal, sizing: SizingResult | None, resources: list[InfrastructureResource], missing: list[str], unsupported: list[str], warnings: list[str]) -> InfrastructureImpactSummary:
        quota = [f"{item.dimension}: projected={item.projected_usage} {item.quota_unit}, compatible={item.compatible}" for item in sizing.quota_impacts] if sizing else []
        capacity = [f"{item.calculation_type}: {item.final_value} {item.result_unit}" for item in sizing.calculations] if sizing else []
        return InfrastructureImpactSummary(proposed_resources=[item.resource_id for item in resources], existing_resources_referenced=[item.related_service_key for item in proposal.components if item.related_service_key], potentially_required_resources=[item.related_service_key for item in proposal.components if item.component_type in {"SERVICE", "DATA_STORE"} and item.related_service_key], configuration_changes=["Generated review files only; no live resource change is made."], associated_environments=[item.name for item in proposal.environments], quota_impact=quota, prerequisites=_dedupe(missing + ["Review and provide all application build inputs and pre-existing service instances before any deployment workflow."]), estimated_capacity=capacity, risks=_dedupe(warnings + [item.risk for item in proposal.risks]), missing_information=_dedupe(missing))

    @staticmethod
    def _review_report(plan_id: uuid.UUID, revision: int, proposal: ArchitectureProposal, sizing: SizingResult | None, status: str, artifacts: list[InfrastructureArtifact], warnings: list[str], missing: list[str], unsupported: list[str]) -> str:
        warning_lines = [f"- {item}" for item in warnings] or ["- None"]
        missing_lines = [f"- {item}" for item in missing] or ["- None"]
        unsupported_lines = [f"- {item}" for item in unsupported] or ["- None"]
        lines = ["# Static infrastructure plan review", "", f"- Plan: `{plan_id}` revision `{revision}`", f"- Architecture proposal: `{proposal.proposal_id}` revision `{proposal.revision}`", f"- Validation status: **{status}**", "- Execution: none; this package is not a dry run and does not compare provider state.", "", "## Files", *[f"- `{item.filename}` ({item.validation_status}, {item.size_bytes} bytes)" for item in artifacts], "", "## Warnings", *warning_lines, "", "## Missing prerequisites", *missing_lines, "", "## Unsupported components", *unsupported_lines, "", "## Security", "- Secrets, tokens, credentials, private keys and raw bindings are excluded. Placeholders are marked for review.", "", "## Validation boundary", "- YAML is locally parsed where tooling is available.", "- Terraform HCL is checked structurally; provider downloads, init, plan and remote state comparison are not performed."]
        return "\n".join(lines) + "\n"
