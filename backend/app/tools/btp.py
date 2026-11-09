"""Read-only BTP tools. Each re-authorizes the resource against the caller's own scope.

``btp_catalog_read`` reports the account mapping stored in this application, which is
not live BTP data. ``btp_applications_list`` asks the live BTP adapter; while no live
integration is configured it reports ``btp_integration_unavailable`` and returns nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.auth.authorization import coverage_for
from app.auth.permissions import Permission
from app.core.exceptions import AppError
from app.integrations.sap_btp.provider import BTPIntegrationUnavailable
from app.models import BTPEnvironment
from app.schemas.architecture import (
    ArchitectureComparisonResponse,
    ArchitectureGenerationRequest,
    ArchitectureProposal,
    ArchitectureRequirements,
    ArchitectureRequirementsAnalysis,
    ArchitectureValidation,
)
from app.schemas.diagnosis import (
    ApplicationDiagnosisRead,
    HealthOverviewRead,
    QuotaDiagnosisRead,
    UnusedResourcePage,
)
from app.schemas.recommendations import (
    RecommendationRequest,
    RecommendationResponse,
    RequirementsAnalysis,
    ServiceComparisonRequest,
    ServiceComparisonResponse,
    ServicePrerequisitesResponse,
    ServiceRequirements,
)
from app.schemas.sizing import (
    SizingCalculationRequest,
    SizingComparisonRequest,
    SizingComparisonResponse,
    SizingRequirementsAnalysis,
    SizingResult,
)
from app.services import audit_service, btp_service
from app.services.audit_service import AuditEventType, Outcome
from app.tools.contracts import (
    OperationType,
    SourceType,
    Tool,
    ToolDenied,
    ToolInput,
    ToolOutput,
    ToolUnavailable,
)
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agents.contracts import AgentContext

CATALOG_TOOL = "btp_catalog_read"
APPLICATIONS_TOOL = "btp_applications_list"
ORGANIZATIONS_TOOL = "btp_organizations_list"
SPACES_TOOL = "btp_spaces_list"
APPLICATION_GET_TOOL = "btp_application_get"
PROCESSES_TOOL = "btp_application_processes_read"
EVENTS_TOOL = "btp_application_events_read"
DIAGNOSE_APPLICATION_TOOL = "btp_application_diagnose"
SERVICE_OFFERINGS_TOOL = "btp_service_offerings_list"
SERVICE_PLANS_TOOL = "btp_service_plans_list"
QUOTAS_TOOL = "btp_quotas_read"
SERVICE_INSTANCES_TOOL = "btp_service_instances_list"
HEALTH_OVERVIEW_TOOL = "btp_health_overview"
APPLICATION_HEALTH_TOOL = "btp_application_health"
QUOTA_DIAGNOSE_TOOL = "btp_quota_diagnose"
RESOURCE_USAGE_TOOL = "btp_resource_usage_analyze"
DESTINATIONS_TOOL = "btp_destinations_list"
ENTITLEMENTS_TOOL = "btp_entitlements_read"
REQUIREMENTS_ANALYZE_TOOL = "btp_requirements_analyze"
SERVICE_RECOMMEND_TOOL = "btp_service_recommend"
SERVICE_COMPARE_TOOL = "btp_service_compare"
SERVICE_PREREQUISITES_TOOL = "btp_service_prerequisites_read"
ARCHITECTURE_REQUIREMENTS_TOOL = "btp_architecture_requirements_analyze"
ARCHITECTURE_DESIGN_TOOL = "btp_architecture_design"
ARCHITECTURE_VALIDATE_TOOL = "btp_architecture_validate"
ARCHITECTURE_COMPARE_TOOL = "btp_architecture_compare"
SIZING_REQUIREMENTS_TOOL = "btp_sizing_requirements_analyze"
RESOURCE_SIZING_TOOL = "btp_resource_sizing_calculate"
SIZING_COMPARE_TOOL = "btp_sizing_scenarios_compare"


class CatalogInput(ToolInput):
    pass


class CatalogAccount(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str
    region: str | None
    source_type: str = "REGISTERED_CONFIGURATION"
    retrieval_status: str = "AVAILABLE"


class CatalogEnvironment(BaseModel):
    id: uuid.UUID
    name: str
    runtime_type: str
    # Only present when the caller can also read the parent subaccount.
    account_id: uuid.UUID | None
    account_name: str | None
    source_type: str = "REGISTERED_CONFIGURATION"
    retrieval_status: str = "AVAILABLE"


class ServiceOfferingInput(ToolInput):
    pass


class ServiceOfferingObservation(BaseModel):
    id: str
    name: str
    description: str | None
    available: bool | None
    service_broker_id: str | None
    entitlement_status: str = "UNKNOWN_ENTITLEMENT"


class ServiceOfferingsOutput(ToolOutput):
    environment_id: uuid.UUID
    provider: str
    observations: list[ServiceOfferingObservation]
    observed_at: str


class ServicePlansInput(ToolInput):
    service_offering_id: str | None = None


class ServicePlanObservation(BaseModel):
    id: str
    name: str
    description: str | None
    available: bool | None
    service_offering_id: str | None
    service_broker_id: str | None
    entitlement_status: str = "UNKNOWN_ENTITLEMENT"


class ServicePlansOutput(ToolOutput):
    environment_id: uuid.UUID
    provider: str
    observations: list[ServicePlanObservation]
    observed_at: str


class QuotasInput(ToolInput):
    quota_type: str = Field(default="all", pattern="^(all|organization|space)$")


class QuotaValueObservation(BaseModel):
    value: int | None
    state: str


class QuotaObservation(BaseModel):
    id: str
    name: str
    quota_type: str
    organization_ids: list[str]
    space_ids: list[str]
    total_memory_mb: QuotaValueObservation
    total_instances: QuotaValueObservation
    total_service_instances: QuotaValueObservation
    total_routes: QuotaValueObservation
    paid_services_allowed: bool | None


class QuotasOutput(ToolOutput):
    environment_id: uuid.UUID
    provider: str
    observations: list[QuotaObservation]
    observed_at: str


class ServiceInstancesInput(ToolInput):
    space_id: str | None = None


class ServiceInstanceObservation(BaseModel):
    id: str
    name: str
    space_id: str | None


class ServiceInstancesOutput(ToolOutput):
    environment_id: uuid.UUID
    provider: str
    observations: list[ServiceInstanceObservation]
    observed_at: str


class CatalogOutput(ToolOutput):
    accounts: list[CatalogAccount]
    environments: list[CatalogEnvironment]


class ApplicationsInput(ToolInput):
    # Kept optional for backwards compatibility; the Phase 12 connector uses the
    # server-selected active environment and never trusts this value as authorization.
    environment_id: uuid.UUID | None = None
    space_id: str | None = None


class Application(BaseModel):
    id: str
    name: str
    state: str | None


class ApplicationsOutput(ToolOutput):
    environment_id: uuid.UUID
    environment_name: str
    provider: str
    applications: list[Application]


class OrganizationInput(ToolInput):
    pass


class OrganizationObservation(BaseModel):
    id: str
    name: str


class OrganizationsOutput(ToolOutput):
    environment_id: uuid.UUID
    organizations: list[OrganizationObservation]


class SpacesInput(ToolInput):
    organization_id: str | None = None


class SpaceObservation(BaseModel):
    id: str
    name: str
    organization_id: str | None


class SpacesOutput(ToolOutput):
    environment_id: uuid.UUID
    spaces: list[SpaceObservation]


class ApplicationGetInput(ToolInput):
    application_id: str


class ApplicationObservation(BaseModel):
    id: str
    name: str
    state: str | None
    lifecycle: str | None
    space_id: str | None


class ApplicationGetOutput(ToolOutput):
    environment_id: uuid.UUID
    application: ApplicationObservation


class ProcessesInput(ToolInput):
    application_id: str


class ProcessObservation(BaseModel):
    id: str
    process_type: str
    state: str | None
    instances: int | None


class ProcessesOutput(ToolOutput):
    environment_id: uuid.UUID
    application_id: str
    processes: list[ProcessObservation]


class EventsInput(ToolInput):
    application_id: str


class EventObservation(BaseModel):
    id: str
    current_state: str | None
    previous_state: str | None
    process_type: str | None
    created_at: str | None


class EventsOutput(ToolOutput):
    environment_id: uuid.UUID
    application_id: str
    events: list[EventObservation]


class DiagnoseApplicationInput(ToolInput):
    application_id: str
    expected_service_instance_ids: list[str] = Field(default_factory=list, max_length=20)


class DiagnosisOutput(ToolOutput):
    diagnosis: ApplicationDiagnosisRead


class HealthOverviewOutput(ToolOutput):
    overview: HealthOverviewRead


class ApplicationHealthInput(ToolInput):
    application_id: str
    expected_running: bool | None = None


class ApplicationHealthOutput(ToolOutput):
    diagnosis: ApplicationDiagnosisRead


class QuotaDiagnoseInput(ToolInput):
    resource_id: str
    resource_type: str = Field(pattern="^(organization|space)$")


class QuotaDiagnoseOutput(ToolOutput):
    diagnosis: QuotaDiagnosisRead


class ResourceUsageInput(ToolInput):
    pass


class ResourceUsageOutput(ToolOutput):
    candidates: UnusedResourcePage


class RequirementsAnalyzeInput(ToolInput):
    request_text: str = Field(min_length=1, max_length=4_000)


class RequirementsAnalyzeOutput(ToolOutput):
    analysis: RequirementsAnalysis


class ServiceRecommendInput(ToolInput):
    request_text: str | None = Field(default=None, min_length=1, max_length=4_000)
    requirements: ServiceRequirements | None = None
    include_live_evidence: bool = False


class ServiceRecommendOutput(ToolOutput):
    response: RecommendationResponse


class ServiceCompareInput(ToolInput):
    service_keys: list[str] = Field(min_length=2, max_length=8)
    request_text: str | None = Field(default=None, max_length=4_000)
    requirements: ServiceRequirements | None = None


class ServiceCompareOutput(ToolOutput):
    response: ServiceComparisonResponse


class ServicePrerequisitesInput(ToolInput):
    service_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9-]+$")


class ServicePrerequisitesOutput(ToolOutput):
    response: ServicePrerequisitesResponse


class ArchitectureRequirementsInput(ToolInput):
    request_text: str = Field(min_length=1, max_length=8_000)


class ArchitectureRequirementsOutput(ToolOutput):
    analysis: ArchitectureRequirementsAnalysis


class ArchitectureDesignInput(ToolInput):
    request_text: str | None = Field(default=None, min_length=1, max_length=8_000)
    requirements: ArchitectureRequirements | None = None
    include_live_evidence: bool = False


class ArchitectureDesignOutput(ToolOutput):
    proposal: ArchitectureProposal


class ArchitectureValidateInput(ToolInput):
    proposal: ArchitectureProposal


class ArchitectureValidateOutput(ToolOutput):
    validation: ArchitectureValidation


class ArchitectureCompareInput(ToolInput):
    proposal: ArchitectureProposal
    alternative_ids: list[str] = Field(default_factory=list, max_length=8)


class ArchitectureCompareOutput(ToolOutput):
    response: ArchitectureComparisonResponse


class SizingRequirementsInput(ToolInput):
    request_text: str = Field(min_length=1, max_length=8_000)


class SizingRequirementsOutput(ToolOutput):
    analysis: SizingRequirementsAnalysis


class ResourceSizingInput(ToolInput):
    request: SizingCalculationRequest


class ResourceSizingOutput(ToolOutput):
    result: SizingResult


class SizingCompareInput(ToolInput):
    request: SizingComparisonRequest


class SizingCompareOutput(ToolOutput):
    response: SizingComparisonResponse


def _read_service(context: AgentContext):
    service = context.btp_read_service
    if service is None:
        raise ToolUnavailable("btp_integration_unavailable")
    if context.settings is None:
        raise ToolUnavailable("btp_integration_unavailable")
    return service


def _recommendation_service(context: AgentContext):
    service = context.btp_recommendation_service
    if service is None:
        raise ToolUnavailable("btp_recommendations_unavailable")
    return service


def _architecture_service(context: AgentContext):
    service = context.btp_architecture_service
    if service is None:
        raise ToolUnavailable("btp_architecture_unavailable")
    return service


def _sizing_service(context: AgentContext):
    service = context.btp_sizing_service
    if service is None:
        raise ToolUnavailable("btp_sizing_unavailable")
    return service


async def _provider_call(operation):
    try:
        return await operation
    except AppError as error:
        if error.status_code in (403, 404):
            raise ToolDenied() from None
        if error.code == "btp_no_active_environment":
            # Preserve the legacy agent contract: no selected live environment is
            # an unavailable integration, while the HTTP connector API keeps its
            # more actionable safe error code.
            raise ToolUnavailable("btp_integration_unavailable") from None
        raise ToolUnavailable(error.code) from None


async def list_organizations(arguments: OrganizationInput, context: AgentContext) -> OrganizationsOutput:
    service = _read_service(context)
    authorized, organizations, _ = await _provider_call(
        service.organizations(context.session, context.principal)
    )
    return OrganizationsOutput(
        environment_id=authorized.context.btp_environment_id,
        organizations=[OrganizationObservation(id=item.id, name=item.name) for item in organizations],
    )


async def list_spaces(arguments: SpacesInput, context: AgentContext) -> SpacesOutput:
    service = _read_service(context)
    authorized, spaces, _ = await _provider_call(
        service.spaces(context.session, context.principal, arguments.organization_id)
    )
    return SpacesOutput(
        environment_id=authorized.context.btp_environment_id,
        spaces=[
            SpaceObservation(id=item.id, name=item.name, organization_id=item.organization_id)
            for item in spaces
        ],
    )


async def get_application(arguments: ApplicationGetInput, context: AgentContext) -> ApplicationGetOutput:
    service = _read_service(context)
    authorized, application = await _provider_call(
        service.application(context.session, context.principal, arguments.application_id)
    )
    return ApplicationGetOutput(
        environment_id=authorized.context.btp_environment_id,
        application=ApplicationObservation(
            id=application.id,
            name=application.name,
            state=application.state,
            lifecycle=application.lifecycle,
            space_id=application.space_id,
        ),
    )


async def read_processes(arguments: ProcessesInput, context: AgentContext) -> ProcessesOutput:
    service = _read_service(context)
    authorized, processes, _ = await _provider_call(
        service.processes(context.session, context.principal, arguments.application_id)
    )
    return ProcessesOutput(
        environment_id=authorized.context.btp_environment_id,
        application_id=arguments.application_id,
        processes=[
            ProcessObservation(
                id=item.id,
                process_type=item.process_type,
                state=item.state,
                instances=item.instances,
            )
            for item in processes
        ],
    )


async def read_events(arguments: EventsInput, context: AgentContext) -> EventsOutput:
    service = _read_service(context)
    authorized, events, _ = await _provider_call(
        service.application_events(context.session, context.principal, arguments.application_id)
    )
    return EventsOutput(
        environment_id=authorized.context.btp_environment_id,
        application_id=arguments.application_id,
        events=[
            EventObservation(
                id=item.id,
                current_state=item.current_state,
                previous_state=item.previous_state,
                process_type=item.process_type,
                created_at=item.created_at.isoformat() if item.created_at else None,
            )
            for item in events
        ],
    )


async def diagnose_application(
    arguments: DiagnoseApplicationInput, context: AgentContext
) -> DiagnosisOutput:
    service = context.btp_diagnosis_service
    if service is None or context.settings is None:
        raise ToolUnavailable("btp_integration_unavailable")
    if context.session_factory is not None:
        await audit_service.record_standalone(
            context.session_factory,
            AuditEventType.BTP_DIAGNOSIS_REQUESTED,
            Outcome.SUCCESS,
            actor_user_id=context.principal.user_id,
            details={"application_id": arguments.application_id},
        )
    try:
        diagnosis = await service.diagnose(
            context.session,
            context.principal,
            arguments.application_id,
            expected_service_instance_ids=arguments.expected_service_instance_ids,
        )
    except AppError as error:
        if context.session_factory is not None:
            await audit_service.record_standalone(
                context.session_factory,
                AuditEventType.BTP_DIAGNOSIS_COMPLETED,
                Outcome.FAILURE,
                actor_user_id=context.principal.user_id,
                details={
                    "application_id": arguments.application_id,
                    "outcome": "failure",
                    "error_code": error.code,
                },
            )
        raise
    if context.session_factory is not None:
        await audit_service.record_standalone(
            context.session_factory,
            AuditEventType.BTP_DIAGNOSIS_COMPLETED,
            Outcome.SUCCESS,
            actor_user_id=context.principal.user_id,
            btp_environment_id=diagnosis.environment_id,
            details={
                "application_id": arguments.application_id,
                "outcome": "success",
                "finding_count": len(diagnosis.findings),
            },
        )
    return DiagnosisOutput(diagnosis=diagnosis)


def _health_service(context: AgentContext):
    service = context.btp_health_service
    if service is None:
        raise ToolUnavailable("btp_health_integration_unavailable")
    if context.settings is None:
        raise ToolUnavailable("btp_health_integration_unavailable")
    return service


async def read_health_overview(arguments: ToolInput, context: AgentContext) -> HealthOverviewOutput:
    service = _health_service(context)
    overview = await _provider_call(service.overview(context.session, context.principal))
    return HealthOverviewOutput(overview=overview)


async def read_application_health(arguments: ApplicationHealthInput, context: AgentContext) -> ApplicationHealthOutput:
    service = _health_service(context)
    diagnosis = await _provider_call(
        service.application_health(
            context.session,
            context.principal,
            arguments.application_id,
            expected_running=arguments.expected_running,
        )
    )
    return ApplicationHealthOutput(diagnosis=diagnosis)


async def diagnose_quota(arguments: QuotaDiagnoseInput, context: AgentContext) -> QuotaDiagnoseOutput:
    service = _health_service(context)
    diagnosis = await _provider_call(
        service.quota_diagnosis(
            context.session, context.principal, arguments.resource_id, arguments.resource_type
        )
    )
    return QuotaDiagnoseOutput(diagnosis=diagnosis)


async def analyze_resource_usage(arguments: ResourceUsageInput, context: AgentContext) -> ResourceUsageOutput:
    service = _health_service(context)
    candidates = await _provider_call(service.unused_resources(context.session, context.principal))
    return ResourceUsageOutput(candidates=candidates)


async def analyze_service_requirements(arguments: RequirementsAnalyzeInput, context: AgentContext) -> RequirementsAnalyzeOutput:
    service = _recommendation_service(context)
    return RequirementsAnalyzeOutput(analysis=service.analyze(arguments.request_text))


async def recommend_services(arguments: ServiceRecommendInput, context: AgentContext) -> ServiceRecommendOutput:
    service = _recommendation_service(context)
    request = RecommendationRequest(
        request_text=arguments.request_text,
        requirements=arguments.requirements,
        include_live_evidence=arguments.include_live_evidence,
        environment_id=context.btp_environment_id,
    )
    response = await service.recommend(
        request,
        session=context.session if arguments.include_live_evidence else None,
        principal=context.principal if arguments.include_live_evidence else None,
    )
    return ServiceRecommendOutput(response=response)


async def compare_services(arguments: ServiceCompareInput, context: AgentContext) -> ServiceCompareOutput:
    service = _recommendation_service(context)
    return ServiceCompareOutput(response=service.compare(ServiceComparisonRequest(
        service_keys=arguments.service_keys, request_text=arguments.request_text, requirements=arguments.requirements
    )))


async def read_service_prerequisites(arguments: ServicePrerequisitesInput, context: AgentContext) -> ServicePrerequisitesOutput:
    service = _recommendation_service(context)
    return ServicePrerequisitesOutput(response=service.prerequisites(arguments.service_key))


async def analyze_architecture_requirements(arguments: ArchitectureRequirementsInput, context: AgentContext) -> ArchitectureRequirementsOutput:
    return ArchitectureRequirementsOutput(analysis=_architecture_service(context).analyze(arguments.request_text))


async def design_architecture(arguments: ArchitectureDesignInput, context: AgentContext) -> ArchitectureDesignOutput:
    service = _architecture_service(context)
    request = ArchitectureGenerationRequest(request_text=arguments.request_text, requirements=arguments.requirements, include_live_evidence=arguments.include_live_evidence, environment_id=context.btp_environment_id)
    proposal = await service.generate(request, session=context.session, principal=context.principal)
    return ArchitectureDesignOutput(proposal=proposal)


async def validate_architecture(arguments: ArchitectureValidateInput, context: AgentContext) -> ArchitectureValidateOutput:
    return ArchitectureValidateOutput(validation=_architecture_service(context).validate(arguments.proposal))


async def compare_architecture(arguments: ArchitectureCompareInput, context: AgentContext) -> ArchitectureCompareOutput:
    from app.schemas.architecture import ArchitectureComparisonRequest

    return ArchitectureCompareOutput(response=_architecture_service(context).compare(ArchitectureComparisonRequest(proposal=arguments.proposal, alternative_ids=arguments.alternative_ids)))


async def analyze_sizing_requirements(arguments: SizingRequirementsInput, context: AgentContext) -> SizingRequirementsOutput:
    return SizingRequirementsOutput(analysis=_sizing_service(context).analyze(arguments.request_text))


async def calculate_resource_sizing(arguments: ResourceSizingInput, context: AgentContext) -> ResourceSizingOutput:
    return ResourceSizingOutput(result=await _sizing_service(context).calculate(context.session, context.principal, arguments.request))


async def compare_sizing_scenarios(arguments: SizingCompareInput, context: AgentContext) -> SizingCompareOutput:
    return SizingCompareOutput(response=_sizing_service(context).compare(arguments.request))


async def read_catalog(arguments: CatalogInput, context: AgentContext) -> CatalogOutput:
    coverage = await coverage_for(context.session, context.principal, Permission.BTP_READ)
    accounts, environments = await btp_service.authorized_catalog(context.session, coverage)
    return CatalogOutput(
        accounts=[
            CatalogAccount(id=a.id, name=a.name, account_type=a.account_type, region=a.region)
            for a in accounts
        ],
        environments=[
            CatalogEnvironment(
                id=e.environment.id,
                name=e.environment.name,
                runtime_type=e.environment.runtime_type,
                account_id=e.account_id,
                account_name=e.account_name,
            )
            for e in environments
        ],
    )


async def list_service_offerings(arguments: ServiceOfferingInput, context: AgentContext) -> ServiceOfferingsOutput:
    service = _read_service(context)
    authorized, items, _ = await _provider_call(
        service.service_offerings(context.session, context.principal)
    )
    return ServiceOfferingsOutput(
        environment_id=authorized.context.btp_environment_id,
        provider="cloud_foundry_v3",
        observations=[
            ServiceOfferingObservation(
                id=item.id,
                name=item.name,
                description=item.description,
                available=item.available,
                service_broker_id=item.service_broker_id,
            )
            for item in items
        ],
        observed_at=datetime.now(UTC).isoformat(),
    )


async def list_service_plans(arguments: ServicePlansInput, context: AgentContext) -> ServicePlansOutput:
    service = _read_service(context)
    authorized, items, _ = await _provider_call(
        service.service_plans(context.session, context.principal, arguments.service_offering_id)
    )
    return ServicePlansOutput(
        environment_id=authorized.context.btp_environment_id,
        provider="cloud_foundry_v3",
        observations=[
            ServicePlanObservation(
                id=item.id,
                name=item.name,
                description=item.description,
                available=item.available,
                service_offering_id=item.service_offering_id,
                service_broker_id=item.service_broker_id,
            )
            for item in items
        ],
        observed_at=datetime.now(UTC).isoformat(),
    )


async def read_quotas(arguments: QuotasInput, context: AgentContext) -> QuotasOutput:
    service = _read_service(context)
    calls = []
    if arguments.quota_type in {"all", "organization"}:
        calls.append(service.organization_quotas(context.session, context.principal))
    if arguments.quota_type in {"all", "space"}:
        calls.append(service.space_quotas(context.session, context.principal))
    batches = await _provider_call(asyncio.gather(*calls))
    authorized = None
    items = []
    for result in batches:
        authorized, batch, _ = result
        items.extend(batch)
    return QuotasOutput(
        environment_id=authorized.context.btp_environment_id,
        provider="cloud_foundry_v3",
        observations=[
            QuotaObservation(
                id=item.id,
                name=item.name,
                quota_type=item.quota_type,
                organization_ids=list(item.organization_ids),
                space_ids=list(item.space_ids),
                total_memory_mb=QuotaValueObservation(value=item.total_memory_mb.value, state=item.total_memory_mb.state),
                total_instances=QuotaValueObservation(value=item.total_instances.value, state=item.total_instances.state),
                total_service_instances=QuotaValueObservation(value=item.total_service_instances.value, state=item.total_service_instances.state),
                total_routes=QuotaValueObservation(value=item.total_routes.value, state=item.total_routes.state),
                paid_services_allowed=item.paid_services_allowed,
            )
            for item in items
        ],
        observed_at=datetime.now(UTC).isoformat(),
    )


async def read_unconfigured_destination_inventory(arguments: ToolInput, context: AgentContext) -> ToolOutput:
    raise ToolUnavailable("btp_destinations_not_configured")


async def read_unconfigured_entitlement_inventory(arguments: ToolInput, context: AgentContext) -> ToolOutput:
    raise ToolUnavailable("btp_entitlements_not_configured")


async def list_service_instances(arguments: ServiceInstancesInput, context: AgentContext) -> ServiceInstancesOutput:
    service = _read_service(context)
    authorized, items, _ = await _provider_call(
        service.service_instances(context.session, context.principal, arguments.space_id)
    )
    return ServiceInstancesOutput(
        environment_id=authorized.context.btp_environment_id,
        provider="cloud_foundry_v3",
        observations=[
            ServiceInstanceObservation(id=item.id, name=item.name, space_id=item.space_id)
            for item in items
        ],
        observed_at=datetime.now(UTC).isoformat(),
    )


async def list_applications(
    arguments: ApplicationsInput, context: AgentContext
) -> ApplicationsOutput:
    if context.btp_read_service is not None:
        service = _read_service(context)
        authorized, applications, _ = await _provider_call(
            service.applications(context.session, context.principal, arguments.space_id)
        )
        return ApplicationsOutput(
            environment_id=authorized.context.btp_environment_id,
            environment_name="active Cloud Foundry environment",
            provider="cloud_foundry_v3",
            applications=[
                Application(id=item.id, name=item.name, state=item.state)
                for item in applications
            ],
        )
    coverage = await coverage_for(context.session, context.principal, Permission.BTP_READ)
    if not coverage.covers_environment(arguments.environment_id):
        raise ToolDenied()
    environment = await context.session.get(BTPEnvironment, arguments.environment_id)
    if environment is None:
        raise ToolDenied()
    provider = context.btp_provider
    try:
        applications = await provider.list_applications(environment)
    except BTPIntegrationUnavailable:
        raise ToolUnavailable("btp_integration_unavailable") from None
    return ApplicationsOutput(
        environment_id=environment.id,
        environment_name=environment.name,
        provider=provider.name,
        applications=[Application(id=a.id, name=a.name, state=a.state) for a in applications],
    )


BTP_TOOLS = (
    Tool(
        name=CATALOG_TOOL,
        description="List the BTP accounts and environments the user is authorized to read "
        "(configured mapping stored in this application).",
        input_model=CatalogInput,
        output_model=CatalogOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.STORED_METADATA,
        run=read_catalog,
    ),
    Tool(
        name=APPLICATIONS_TOOL,
        description="List applications in one authorized environment via the live BTP adapter.",
        input_model=ApplicationsInput,
        output_model=ApplicationsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_applications,
        timeout_seconds=30.0,
    ),
    Tool(
        name=ORGANIZATIONS_TOOL,
        description="List organizations from the active authorized Cloud Foundry environment.",
        input_model=OrganizationInput,
        output_model=OrganizationsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_organizations,
    ),
    Tool(
        name=SPACES_TOOL,
        description="List explicitly authorized spaces from the active Cloud Foundry environment.",
        input_model=SpacesInput,
        output_model=SpacesOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_spaces,
    ),
    Tool(
        name=APPLICATION_GET_TOOL,
        description="Read one application from an explicitly authorized Cloud Foundry space.",
        input_model=ApplicationGetInput,
        output_model=ApplicationGetOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=get_application,
    ),
    Tool(
        name=PROCESSES_TOOL,
        description="Read an application's Cloud Foundry processes without commands or secrets.",
        input_model=ProcessesInput,
        output_model=ProcessesOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_processes,
    ),
    Tool(
        name=EVENTS_TOOL,
        description="Read bounded Cloud Foundry application usage events for an authorized app.",
        input_model=EventsInput,
        output_model=EventsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_events,
    ),
    Tool(
        name=DIAGNOSE_APPLICATION_TOOL,
        description=(
            "Diagnose one authorized Cloud Foundry application using bounded read-only "
            "evidence; no infrastructure changes or secrets are available."
        ),
        input_model=DiagnoseApplicationInput,
        output_model=DiagnosisOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=diagnose_application,
        timeout_seconds=30.0,
    ),
    Tool(
        name=SERVICE_OFFERINGS_TOOL,
        description="List safe Cloud Foundry service offering metadata from the provider catalog; entitlement is not inferred.",
        input_model=ServiceOfferingInput,
        output_model=ServiceOfferingsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_service_offerings,
    ),
    Tool(
        name=SERVICE_PLANS_TOOL,
        description="List safe Cloud Foundry service plan metadata from the provider catalog; entitlement is not inferred.",
        input_model=ServicePlansInput,
        output_model=ServicePlansOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_service_plans,
    ),
    Tool(
        name=QUOTAS_TOOL,
        description="Read authorized Cloud Foundry organization and space quotas with explicit unknown and unlimited semantics.",
        input_model=QuotasInput,
        output_model=QuotasOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_quotas,
    ),
    Tool(
        name=SERVICE_INSTANCES_TOOL,
        description="List authorized Cloud Foundry service-instance metadata without credentials or parameters.",
        input_model=ServiceInstancesInput,
        output_model=ServiceInstancesOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=list_service_instances,
    ),
    Tool(
        name=DESTINATIONS_TOOL,
        description="Read safe SAP Destination Service metadata only when a dedicated authorized adapter is configured.",
        input_model=ToolInput,
        output_model=ToolOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_unconfigured_destination_inventory,
    ),
    Tool(
        name=ENTITLEMENTS_TOOL,
        description="Read safe SAP entitlement metadata only when a dedicated official adapter is configured.",
        input_model=ToolInput,
        output_model=ToolOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_unconfigured_entitlement_inventory,
    ),
    Tool(
        name=HEALTH_OVERVIEW_TOOL,
        description="Summarize bounded deterministic health findings across authorized applications.",
        input_model=ToolInput,
        output_model=HealthOverviewOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_health_overview,
        timeout_seconds=30.0,
    ),
    Tool(
        name=APPLICATION_HEALTH_TOOL,
        description="Diagnose one authorized Cloud Foundry application using deterministic evidence.",
        input_model=ApplicationHealthInput,
        output_model=ApplicationHealthOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=read_application_health,
        timeout_seconds=30.0,
    ),
    Tool(
        name=QUOTA_DIAGNOSE_TOOL,
        description="Diagnose one authorized organization or space quota using compatible provider usage units.",
        input_model=QuotaDiagnoseInput,
        output_model=QuotaDiagnoseOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=diagnose_quota,
        timeout_seconds=30.0,
    ),
    Tool(
        name=RESOURCE_USAGE_TOOL,
        description="Identify potentially unused service candidates without recommending deletion.",
        input_model=ResourceUsageInput,
        output_model=ResourceUsageOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.LIVE_BTP,
        run=analyze_resource_usage,
        timeout_seconds=30.0,
    ),
    Tool(
        name=REQUIREMENTS_ANALYZE_TOOL,
        description="Extract explicit business and technical requirements without inventing workload or budget data.",
        input_model=RequirementsAnalyzeInput,
        output_model=RequirementsAnalyzeOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=analyze_service_requirements,
    ),
    Tool(
        name=SERVICE_RECOMMEND_TOOL,
        description="Recommend documented SAP BTP service families using deterministic capability and compatibility checks.",
        input_model=ServiceRecommendInput,
        output_model=ServiceRecommendOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.DOCUMENTATION,
        run=recommend_services,
        timeout_seconds=30.0,
    ),
    Tool(
        name=SERVICE_COMPARE_TOOL,
        description="Compare only verified reference-catalog services; no prices or unsupported plans are inferred.",
        input_model=ServiceCompareInput,
        output_model=ServiceCompareOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.DOCUMENTATION,
        run=compare_services,
    ),
    Tool(
        name=SERVICE_PREREQUISITES_TOOL,
        description="Read documented prerequisites, limitations and alternatives for a verified SAP BTP service.",
        input_model=ServicePrerequisitesInput,
        output_model=ServicePrerequisitesOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.DOCUMENTATION,
        run=read_service_prerequisites,
    ),
    Tool(
        name=ARCHITECTURE_REQUIREMENTS_TOOL,
        description="Extract typed architecture requirements and missing information without inventing customer facts.",
        input_model=ArchitectureRequirementsInput,
        output_model=ArchitectureRequirementsOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=analyze_architecture_requirements,
    ),
    Tool(
        name=ARCHITECTURE_DESIGN_TOOL,
        description="Generate a validated, read-only SAP BTP architecture proposal from documented service recommendations.",
        input_model=ArchitectureDesignInput,
        output_model=ArchitectureDesignOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.DOCUMENTATION,
        run=design_architecture,
        timeout_seconds=30.0,
    ),
    Tool(
        name=ARCHITECTURE_VALIDATE_TOOL,
        description="Validate architecture component identities, references, security controls and evidence without executing changes.",
        input_model=ArchitectureValidateInput,
        output_model=ArchitectureValidateOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=validate_architecture,
    ),
    Tool(
        name=ARCHITECTURE_COMPARE_TOOL,
        description="Compare validated architecture alternatives using explicit criteria and documented evidence.",
        input_model=ArchitectureCompareInput,
        output_model=ArchitectureCompareOutput,
        required_permission=Permission.BTP_READ,
        operation_type=OperationType.READ,
        source_type=SourceType.DOCUMENTATION,
        run=compare_architecture,
    ),
    Tool(
        name=SIZING_REQUIREMENTS_TOOL,
        description="Parse deterministic sizing inputs and identify missing workload evidence without inventing values.",
        input_model=SizingRequirementsInput,
        output_model=SizingRequirementsOutput,
        required_permission=Permission.CHAT_USE,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=analyze_sizing_requirements,
    ),
    Tool(
        name=RESOURCE_SIZING_TOOL,
        description="Calculate reproducible memory, instance, disk, storage and quota scenarios using the server formula registry.",
        input_model=ResourceSizingInput,
        output_model=ResourceSizingOutput,
        required_permission=Permission.CHAT_USE,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=calculate_resource_sizing,
        timeout_seconds=30.0,
    ),
    Tool(
        name=SIZING_COMPARE_TOOL,
        description="Compare typed sizing scenarios with the same deterministic formula registry.",
        input_model=SizingCompareInput,
        output_model=SizingCompareOutput,
        required_permission=Permission.CHAT_USE,
        operation_type=OperationType.READ,
        source_type=SourceType.CALCULATION,
        run=compare_sizing_scenarios,
    ),
)


def register_btp_tools(registry: ToolRegistry) -> None:
    for tool in BTP_TOOLS:
        registry.register(tool)
