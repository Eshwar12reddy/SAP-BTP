"""Owner-scoped, static SAP BTP infrastructure plan APIs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.infrastructure_plan import (
    InfrastructureArtifactResponse,
    InfrastructurePlan,
    InfrastructurePlanCompareResponse,
    InfrastructurePlanRequirements,
    InfrastructurePlanRequirementsAnalysis,
    InfrastructureRevisionPage,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

router = APIRouter(prefix="/btp/infrastructure-plans", tags=["btp-infrastructure-plans"])
ChatUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]


def _service(request: Request):
    return request.app.state.infrastructure_plan_service


@router.post("/requirements/analyze", response_model=InfrastructurePlanRequirementsAnalysis)
async def analyze_requirements(payload: InfrastructurePlanRequirements, request: Request, session: SessionDep, principal: ChatUser):
    return await _service(request).analyze(session, principal, payload)


@router.post("/generate", response_model=InfrastructurePlan)
async def generate_plan(payload: InfrastructurePlanRequirements, request: Request, session: SessionDep, principal: ChatUser):
    plan = await _service(request).generate(session, principal, payload)
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_INFRASTRUCTURE_PLAN_GENERATED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_environment_id=plan.target_environment_id, details={"operation": "infrastructure_plan_generate", "plan_id": str(plan.plan_id), "revision": plan.revision, "output_format": plan.output_format, "validation_status": plan.validation_status})
    return plan


@router.post("/validate", response_model=InfrastructurePlan)
async def validate_plan(payload: InfrastructurePlan, request: Request, principal: ChatUser):
    result = _service(request).validate(payload)
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_INFRASTRUCTURE_PLAN_VALIDATED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_environment_id=result.target_environment_id, details={"operation": "infrastructure_plan_validate", "plan_id": str(result.plan_id), "revision": result.revision, "validation_status": result.validation_status})
    return result


@router.get("/{plan_id}", response_model=InfrastructurePlan)
async def get_plan(plan_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser, revision: Annotated[int | None, Query(ge=1)] = None):
    return await _service(request).get(session, principal, plan_id, revision)


@router.get("/{plan_id}/revisions", response_model=InfrastructureRevisionPage)
async def list_revisions(plan_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser):
    return await _service(request).revisions(session, principal, plan_id)


@router.post("/{plan_id}/compare", response_model=InfrastructurePlanCompareResponse)
async def compare_revisions(plan_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser, left_revision: Annotated[int, Query(ge=1)], right_revision: Annotated[int, Query(ge=1)]):
    return await _service(request).compare(session, principal, plan_id, left_revision, right_revision)


@router.get("/{plan_id}/artifacts/{filename}", response_model=InfrastructureArtifactResponse)
async def get_artifact(plan_id: uuid.UUID, filename: str, request: Request, session: SessionDep, principal: ChatUser, revision: Annotated[int | None, Query(ge=1)] = None):
    return await _service(request).artifact(session, principal, plan_id, filename, revision)


@router.get("/{plan_id}/export")
async def export_package(plan_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser, revision: Annotated[int | None, Query(ge=1)] = None):
    content = await _service(request).export_zip(session, principal, plan_id, revision)
    return Response(content=content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="infrastructure-plan-{plan_id}.zip"'})
