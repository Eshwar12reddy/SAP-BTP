"""Deterministic, read-only SAP BTP sizing APIs."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.sizing import (
    SizingCalculationRequest,
    SizingComparisonRequest,
    SizingComparisonResponse,
    SizingRequirementsAnalysis,
    SizingResult,
    SizingResultPage,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

router = APIRouter(prefix="/btp/sizing", tags=["btp-sizing"])
ChatUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]


class RequirementsAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_text: str = Field(min_length=1, max_length=8_000)


def _service(request: Request):
    return request.app.state.sizing_service


@router.post("/requirements/analyze", response_model=SizingRequirementsAnalysis)
async def analyze_requirements(payload: RequirementsAnalyzeRequest, request: Request, principal: ChatUser) -> SizingRequirementsAnalysis:
    return _service(request).analyze(payload.request_text)


@router.post("/calculate", response_model=SizingResult)
async def calculate(payload: SizingCalculationRequest, request: Request, session: SessionDep, principal: ChatUser) -> SizingResult:
    result = await _service(request).calculate(session, principal, payload)
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_SIZING_COMPLETED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_environment_id=payload.requirements.target_environment_id if payload.requirements else None, details={"operation": "sizing_calculate", "result_id": str(result.result_id), "scenario_id": result.scenario_id, "status": result.status})
    return result


@router.post("/compare", response_model=SizingComparisonResponse)
async def compare(payload: SizingComparisonRequest, request: Request, principal: ChatUser) -> SizingComparisonResponse:
    return _service(request).compare(payload)


@router.get("", response_model=SizingResultPage)
async def list_results(request: Request, session: SessionDep, principal: ChatUser, limit: Annotated[int, Query(ge=1, le=50)] = 20, offset: Annotated[int, Query(ge=0, le=10_000)] = 0) -> SizingResultPage:
    return await _service(request).list(session, principal, limit, offset)


@router.get("/{result_id}", response_model=SizingResult)
async def get_result(result_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser) -> SizingResult:
    return await _service(request).get(session, principal, result_id)


@router.post("/architecture/{proposal_id}/calculate", response_model=SizingResult)
async def calculate_for_architecture(proposal_id: uuid.UUID, payload: SizingCalculationRequest, request: Request, session: SessionDep, principal: ChatUser) -> SizingResult:
    requirements = payload.requirements.model_copy(update={"architecture_proposal_id": proposal_id}) if payload.requirements else _service(request).analyze(payload.request_text or "").requirements.model_copy(update={"architecture_proposal_id": proposal_id})
    result = await _service(request).calculate(session, principal, payload.model_copy(update={"requirements": requirements}))
    return result
