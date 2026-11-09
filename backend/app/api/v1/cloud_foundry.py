"""Authorized read-only Cloud Foundry inventory endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.cloud_foundry import (
    ApplicationEventPage,
    ApplicationEventRead,
    ApplicationPage,
    ApplicationRead,
    InventoryStatusRead,
    OrganizationPage,
    OrganizationRead,
    ProcessPage,
    ProcessRead,
    ProcessStatRead,
    ProcessStatsRead,
    QuotaPage,
    QuotaRead,
    QuotaValueRead,
    ServiceBindingPage,
    ServiceBindingRead,
    ServiceInstancePage,
    ServiceInstanceRead,
    ServiceOfferingPage,
    ServiceOfferingRead,
    ServicePlanPage,
    ServicePlanRead,
    SpacePage,
    SpaceRead,
    UsageSummaryRead,
)
from app.schemas.diagnosis import (
    ApplicationDiagnosisRead,
    HealthOverviewRead,
    QuotaDiagnosisRead,
    UnusedResourcePage,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome
from app.services.cloud_foundry_service import AuthorizedCloudFoundryService
from app.services.diagnosis_service import CloudFoundryDiagnosisService, explain_diagnosis
from app.services.enterprise_inventory_service import EnterpriseInventoryService
from app.services.health_diagnosis_service import HealthDiagnosisService

router = APIRouter(prefix="/btp/runtime", tags=["btp-cloud-foundry"])
Reader = Annotated[Principal, Depends(require_permission(Permission.BTP_READ))]
ResourceId = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")]
ExpectedServiceIds = Annotated[list[str] | None, Query(max_length=20)]


def _quota_value(value) -> QuotaValueRead:
    return QuotaValueRead(value=value.value, state=value.state)


def _quota_read(item, evidence) -> QuotaRead:
    return QuotaRead(
        id=item.id,
        quota_type=item.quota_type,
        display_name=item.name,
        organization_ids=list(item.organization_ids),
        space_ids=list(item.space_ids),
        total_memory_mb=_quota_value(item.total_memory_mb),
        per_process_memory_mb=_quota_value(item.per_process_memory_mb),
        total_instances=_quota_value(item.total_instances),
        per_app_tasks=_quota_value(item.per_app_tasks),
        total_service_instances=_quota_value(item.total_service_instances),
        total_service_keys=_quota_value(item.total_service_keys),
        total_routes=_quota_value(item.total_routes),
        total_reserved_ports=_quota_value(item.total_reserved_ports),
        total_domains=_quota_value(item.total_domains),
        log_rate_limit_bytes_per_second=_quota_value(item.log_rate_limit_bytes_per_second),
        paid_services_allowed=item.paid_services_allowed,
        created_at=item.created_at,
        updated_at=item.updated_at,
        evidence=evidence,
    )


def _service(request: Request) -> AuthorizedCloudFoundryService:
    return AuthorizedCloudFoundryService(
        request.app.state.settings,
        transport=getattr(request.app.state, "cloud_foundry_transport", None),
    )


def _diagnosis_service(request: Request) -> CloudFoundryDiagnosisService:
    return CloudFoundryDiagnosisService(
        request.app.state.settings,
        transport=getattr(request.app.state, "cloud_foundry_transport", None),
    )


def _health_service(request: Request) -> HealthDiagnosisService:
    return HealthDiagnosisService(
        request.app.state.settings,
        transport=getattr(request.app.state, "cloud_foundry_transport", None),
    )


@router.get("/organizations", response_model=OrganizationPage)
async def organizations(
    request: Request, session: SessionDep, principal: Reader
) -> OrganizationPage:
    service = _service(request)
    authorized, items, more = await service.organizations(session, principal)
    evidence = service.evidence(authorized)
    return OrganizationPage(
        items=[
            OrganizationRead(
                id=item.id,
                display_name=item.name,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/organizations/{organization_id}", response_model=OrganizationRead)
async def organization(
    organization_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> OrganizationRead:
    service = _service(request)
    authorized, item = await service.organization(session, principal, organization_id)
    return OrganizationRead(
        id=item.id,
        display_name=item.name,
        created_at=item.created_at,
        updated_at=item.updated_at,
        evidence=service.evidence(authorized),
    )


@router.get("/spaces", response_model=SpacePage)
async def spaces(
    request: Request,
    session: SessionDep,
    principal: Reader,
    organization_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")] = None,
) -> SpacePage:
    service = _service(request)
    authorized, items, more = await service.spaces(session, principal, organization_id)
    evidence = service.evidence(authorized)
    return SpacePage(
        items=[
            SpaceRead(
                id=item.id,
                display_name=item.name,
                organization_id=item.organization_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/spaces/{space_id}", response_model=SpaceRead)
async def space(
    space_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> SpaceRead:
    service = _service(request)
    authorized, item = await service.space(session, principal, space_id)
    return SpaceRead(
        id=item.id,
        display_name=item.name,
        organization_id=item.organization_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        evidence=service.evidence(authorized),
    )


@router.get("/applications", response_model=ApplicationPage)
async def applications(
    request: Request,
    session: SessionDep,
    principal: Reader,
    space_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")] = None,
) -> ApplicationPage:
    service = _service(request)
    authorized, items, more = await service.applications(session, principal, space_id)
    evidence = service.evidence(authorized)
    return ApplicationPage(
        items=[
            ApplicationRead(
                id=item.id,
                display_name=item.name,
                lifecycle_state=item.state,
                lifecycle=item.lifecycle,
                space_id=item.space_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/applications/{application_id}", response_model=ApplicationRead)
async def application(
    application_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> ApplicationRead:
    service = _service(request)
    authorized, item = await service.application(session, principal, application_id)
    return ApplicationRead(
        id=item.id,
        display_name=item.name,
        lifecycle_state=item.state,
        lifecycle=item.lifecycle,
        space_id=item.space_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
        evidence=service.evidence(authorized),
    )


@router.get("/applications/{application_id}/processes", response_model=ProcessPage)
async def processes(
    application_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> ProcessPage:
    service = _service(request)
    authorized, items, more = await service.processes(session, principal, application_id)
    evidence = service.evidence(authorized)
    return ProcessPage(
        items=[
            ProcessRead(
                id=item.id,
                process_type=item.process_type,
                state=item.state,
                instances=item.instances,
                application_id=item.app_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/processes/{process_id}/stats", response_model=ProcessStatsRead)
async def process_stats(
    process_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> ProcessStatsRead:
    service = _service(request)
    authorized, items = await service.process_stats(session, principal, process_id)
    evidence = service.evidence(authorized)
    return ProcessStatsRead(
        process_id=process_id,
        items=[
            ProcessStatRead(
                process_type=item.process_type,
                index=item.index,
                state=item.state,
                observed_at=item.observed_at,
                cpu=item.cpu,
                memory_bytes=item.memory_bytes,
                disk_bytes=item.disk_bytes,
                uptime_seconds=item.uptime_seconds,
                evidence=evidence,
            )
            for item in items
        ],
        evidence=evidence,
    )


@router.get("/service-instances", response_model=ServiceInstancePage)
async def service_instances(
    request: Request,
    session: SessionDep,
    principal: Reader,
    space_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")] = None,
) -> ServiceInstancePage:
    service = _service(request)
    authorized, items, more = await service.service_instances(session, principal, space_id)
    evidence = service.evidence(authorized)
    return ServiceInstancePage(
        items=[
            ServiceInstanceRead(
                id=item.id,
                display_name=item.name,
                space_id=item.space_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/service-offerings", response_model=ServiceOfferingPage)
async def service_offerings(
    request: Request, session: SessionDep, principal: Reader
) -> ServiceOfferingPage:
    service = _service(request)
    authorized, items, more = await service.service_offerings(session, principal)
    evidence = service.evidence(authorized)
    return ServiceOfferingPage(
        items=[
            ServiceOfferingRead(
                id=item.id,
                display_name=item.name,
                description=item.description,
                available=item.available,
                service_broker_id=item.service_broker_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/service-plans", response_model=ServicePlanPage)
async def service_plans(
    request: Request,
    session: SessionDep,
    principal: Reader,
    service_offering_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")] = None,
) -> ServicePlanPage:
    service = _service(request)
    authorized, items, more = await service.service_plans(session, principal, service_offering_id)
    evidence = service.evidence(authorized)
    return ServicePlanPage(
        items=[
            ServicePlanRead(
                id=item.id,
                display_name=item.name,
                description=item.description,
                available=item.available,
                service_offering_id=item.service_offering_id,
                service_broker_id=item.service_broker_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/organization-quotas", response_model=QuotaPage)
async def organization_quotas(
    request: Request, session: SessionDep, principal: Reader
) -> QuotaPage:
    service = _service(request)
    authorized, items, more = await service.organization_quotas(session, principal)
    evidence = service.evidence(authorized)
    return QuotaPage(
        items=[_quota_read(item, evidence) for item in items],
        has_more=more,
        evidence=evidence,
    )


@router.get("/space-quotas", response_model=QuotaPage)
async def space_quotas(
    request: Request, session: SessionDep, principal: Reader
) -> QuotaPage:
    service = _service(request)
    authorized, items, more = await service.space_quotas(session, principal)
    evidence = service.evidence(authorized)
    return QuotaPage(
        items=[_quota_read(item, evidence) for item in items],
        has_more=more,
        evidence=evidence,
    )


@router.get("/organizations/{organization_id}/usage-summary", response_model=UsageSummaryRead)
async def organization_usage_summary(
    organization_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> UsageSummaryRead:
    service = _service(request)
    authorized, item = await service.organization_usage_summary(session, principal, organization_id)
    return UsageSummaryRead(
        resource_id=item.resource_id,
        resource_type=item.resource_type,
        started_instances=item.started_instances,
        memory_in_mb=item.memory_in_mb,
        evidence=service.evidence(authorized),
    )


@router.get("/spaces/{space_id}/usage-summary", response_model=UsageSummaryRead)
async def space_usage_summary(
    space_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> UsageSummaryRead:
    service = _service(request)
    authorized, item = await service.space_usage_summary(session, principal, space_id)
    return UsageSummaryRead(
        resource_id=item.resource_id,
        resource_type=item.resource_type,
        started_instances=item.started_instances,
        memory_in_mb=item.memory_in_mb,
        evidence=service.evidence(authorized),
    )


@router.get("/destinations", response_model=InventoryStatusRead)
async def destinations(
    request: Request, session: SessionDep, principal: Reader
) -> InventoryStatusRead:
    # Status is explicit and safe even when no active CF connection exists. No URL or
    # Cloud Foundry token is used for this separate SAP service boundary.
    return EnterpriseInventoryService.destinations()


@router.get("/entitlements", response_model=InventoryStatusRead)
async def entitlements(
    request: Request, session: SessionDep, principal: Reader
) -> InventoryStatusRead:
    return EnterpriseInventoryService.entitlements()


@router.get("/health/overview", response_model=HealthOverviewRead)
async def health_overview(
    request: Request, session: SessionDep, principal: Reader
) -> HealthOverviewRead:
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_HEALTH_REQUESTED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"operation": "health_overview"},
    )
    service = _health_service(request)
    try:
        result = await service.overview(session, principal)
    except Exception as error:
        await audit_service.record_standalone(
            request.app.state.session_factory,
            AuditEventType.BTP_HEALTH_COMPLETED,
            Outcome.FAILURE,
            actor_user_id=principal.user_id,
            details={"operation": "health_overview", "error_code": getattr(error, "code", "health_failed")},
        )
        raise
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_HEALTH_COMPLETED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_environment_id=result.environment_id,
        details={"operation": "health_overview", "finding_count": len(result.findings)},
    )
    return result


@router.get("/applications/{application_id}/health", response_model=ApplicationDiagnosisRead)
async def application_health(
    application_id: ResourceId,
    request: Request,
    session: SessionDep,
    principal: Reader,
    expected_running: bool | None = None,
) -> ApplicationDiagnosisRead:
    return await _health_service(request).application_health(
        session, principal, application_id, expected_running=expected_running
    )


@router.get("/organizations/{organization_id}/quota-diagnosis", response_model=QuotaDiagnosisRead)
async def organization_quota_diagnosis(
    organization_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> QuotaDiagnosisRead:
    return await _health_service(request).quota_diagnosis(
        session, principal, organization_id, "organization"
    )


@router.get("/spaces/{space_id}/quota-diagnosis", response_model=QuotaDiagnosisRead)
async def space_quota_diagnosis(
    space_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> QuotaDiagnosisRead:
    return await _health_service(request).quota_diagnosis(session, principal, space_id, "space")


@router.get("/unused-resources", response_model=UnusedResourcePage)
async def unused_resources(
    request: Request, session: SessionDep, principal: Reader
) -> UnusedResourcePage:
    return await _health_service(request).unused_resources(session, principal)


@router.get("/applications/{application_id}/service-bindings", response_model=ServiceBindingPage)
async def service_bindings(
    application_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> ServiceBindingPage:
    service = _service(request)
    authorized, items, more = await service.service_bindings(session, principal, application_id)
    evidence = service.evidence(authorized)
    return ServiceBindingPage(
        items=[
            ServiceBindingRead(
                id=item.id,
                binding_type=item.binding_type,
                application_id=item.app_id,
                service_instance_id=item.service_instance_id,
                created_at=item.created_at,
                updated_at=item.updated_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get("/applications/{application_id}/events", response_model=ApplicationEventPage)
async def application_events(
    application_id: ResourceId, request: Request, session: SessionDep, principal: Reader
) -> ApplicationEventPage:
    service = _service(request)
    authorized, items, more = await service.application_events(session, principal, application_id)
    evidence = service.evidence(authorized)
    return ApplicationEventPage(
        items=[
            ApplicationEventRead(
                id=item.id,
                event_type=item.event_type,
                application_id=item.app_id,
                application_name=item.app_name,
                process_type=item.process_type,
                current_state=item.current_state,
                previous_state=item.previous_state,
                instance_count=item.instance_count,
                created_at=item.created_at,
                evidence=evidence,
            )
            for item in items
        ],
        has_more=more,
        evidence=evidence,
    )


@router.get(
    "/applications/{application_id}/diagnosis",
    response_model=ApplicationDiagnosisRead,
)
async def diagnose_application(
    application_id: ResourceId,
    request: Request,
    session: SessionDep,
    principal: Reader,
    expected_service_instance_id: ExpectedServiceIds = None,
    expected_running: bool | None = None,
) -> ApplicationDiagnosisRead:
    """Return deterministic, read-only findings for one authorized application."""
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_DIAGNOSIS_REQUESTED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"application_id": application_id},
    )
    service = _diagnosis_service(request)
    try:
        diagnosis = await service.diagnose(
            session,
            principal,
            application_id,
            expected_service_instance_ids=expected_service_instance_id,
            expected_running=expected_running,
        )
        explanation = await explain_diagnosis(
            diagnosis,
            getattr(request.app.state, "deepseek_client", None),
            request.app.state.redactor,
            request.app.state.settings,
        )
        if explanation:
            diagnosis = diagnosis.model_copy(update={"ai_explanation": explanation})
    except Exception as error:
        code = getattr(error, "code", "diagnosis_failed")
        await audit_service.record_standalone(
            request.app.state.session_factory,
            AuditEventType.BTP_DIAGNOSIS_COMPLETED,
            Outcome.FAILURE,
            actor_user_id=principal.user_id,
            details={"application_id": application_id, "outcome": "failure", "error_code": code},
        )
        raise
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_DIAGNOSIS_COMPLETED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_environment_id=diagnosis.environment_id,
        details={
            "application_id": application_id,
            "outcome": "success",
            "finding_count": len(diagnosis.findings),
        },
    )
    return diagnosis
