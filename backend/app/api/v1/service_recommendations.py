"""Authorized service recommendation APIs.

Reference recommendations require only chat authorization.  Live catalog enrichment is
performed only when requested and is constrained by the caller's BTP read scope and the
server-selected environment.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.recommendations import (
    RecommendationRequest,
    RecommendationResponse,
    RequirementsAnalysis,
    ServiceComparisonRequest,
    ServiceComparisonResponse,
    ServicePrerequisitesResponse,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome
from app.services.service_recommendation_service import (
    analyze_requirements as analyze_requirements_text,
)

router = APIRouter(prefix="/btp/service-recommendations", tags=["btp-service-recommendations"])
ChatUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]


class RequirementsAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_text: str = Field(min_length=1, max_length=4_000)


class PrerequisitesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9-]+$")


def _service(request: Request):
    return request.app.state.service_recommendation_service


@router.post("/requirements/analyze", response_model=RequirementsAnalysis)
async def analyze_requirements(payload: RequirementsAnalyzeRequest, principal: ChatUser) -> RequirementsAnalysis:
    return analyze_requirements_text(payload.request_text)


@router.post("/recommend", response_model=RecommendationResponse)
async def recommend_services(
    payload: RecommendationRequest,
    request: Request,
    session: SessionDep,
    principal: ChatUser,
) -> RecommendationResponse:
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_RECOMMENDATION_REQUESTED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_environment_id=payload.environment_id,
        details={"operation": "service_recommendation", "live_requested": payload.include_live_evidence},
    )
    result = await _service(request).recommend(
        payload,
        session=session if payload.include_live_evidence else None,
        principal=principal if payload.include_live_evidence else None,
    )
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_RECOMMENDATION_COMPLETED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_environment_id=result.live_catalog.environment_id,
        details={"operation": "service_recommendation", "recommendation_count": len(result.recommendations)},
    )
    return result


@router.post("/compare", response_model=ServiceComparisonResponse)
async def compare_services(payload: ServiceComparisonRequest, request: Request, principal: ChatUser) -> ServiceComparisonResponse:
    return _service(request).compare(payload)


@router.post("/prerequisites", response_model=ServicePrerequisitesResponse)
async def read_prerequisites(payload: PrerequisitesRequest, request: Request, principal: ChatUser) -> ServicePrerequisitesResponse:
    return _service(request).prerequisites(payload.service_key)
