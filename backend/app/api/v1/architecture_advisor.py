"""Authorized architecture advisor endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.architecture import (
    ArchitectureComparisonRequest,
    ArchitectureComparisonResponse,
    ArchitectureGenerationRequest,
    ArchitectureGraphResponse,
    ArchitectureProposal,
    ArchitectureProposalPage,
    ArchitectureRequirementsAnalysis,
    ArchitectureRevisionRequest,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

router = APIRouter(prefix="/btp/architecture-advisor", tags=["btp-architecture-advisor"])
ChatUser = Annotated[Principal, Depends(require_permission(Permission.CHAT_USE))]


def _service(request: Request):
    return request.app.state.architecture_advisor_service


@router.post("/requirements/analyze", response_model=ArchitectureRequirementsAnalysis)
async def analyze_requirements(payload: ArchitectureGenerationRequest, request: Request, principal: ChatUser) -> ArchitectureRequirementsAnalysis:
    if payload.requirements is not None:
        return _service(request).analyze_typed(payload.requirements)
    return _service(request).analyze(payload.request_text or "")


@router.post("/proposals", response_model=ArchitectureProposal)
async def create_proposal(payload: ArchitectureGenerationRequest, request: Request, session: SessionDep, principal: ChatUser) -> ArchitectureProposal:
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_ARCHITECTURE_REQUESTED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_account_id=payload.account_id, btp_environment_id=payload.environment_id, details={"operation": "architecture_create"})
    proposal = await _service(request).create(session, principal, payload)
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_ARCHITECTURE_COMPLETED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_account_id=proposal.account_id, btp_environment_id=proposal.environment_id, details={"operation": "architecture_create", "proposal_id": str(proposal.proposal_id), "revision": proposal.revision})
    return proposal


@router.get("/proposals", response_model=ArchitectureProposalPage)
async def list_proposals(request: Request, session: SessionDep, principal: ChatUser, limit: Annotated[int, Query(ge=1, le=50)] = 20, offset: Annotated[int, Query(ge=0, le=10_000)] = 0) -> ArchitectureProposalPage:
    items, has_more = await _service(request).list(session, principal, limit, offset)
    return ArchitectureProposalPage(items=items, has_more=has_more)


@router.get("/proposals/{proposal_id}", response_model=ArchitectureProposal)
async def get_proposal(proposal_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser) -> ArchitectureProposal:
    return await _service(request).get(session, principal, proposal_id)


@router.post("/proposals/{proposal_id}/revisions", response_model=ArchitectureProposal)
async def revise_proposal(proposal_id: uuid.UUID, payload: ArchitectureRevisionRequest, request: Request, session: SessionDep, principal: ChatUser) -> ArchitectureProposal:
    proposal = await _service(request).revise(session, principal, proposal_id, payload)
    await audit_service.record_standalone(request.app.state.session_factory, AuditEventType.BTP_ARCHITECTURE_COMPLETED, Outcome.SUCCESS, actor_user_id=principal.user_id, btp_account_id=proposal.account_id, btp_environment_id=proposal.environment_id, details={"operation": "architecture_revision", "proposal_id": str(proposal.proposal_id), "revision": proposal.revision})
    return proposal


@router.post("/validate", response_model=ArchitectureProposal)
async def validate_proposal(payload: ArchitectureProposal, request: Request, principal: ChatUser) -> ArchitectureProposal:
    validation = _service(request).validate(payload)
    return payload.model_copy(update={"validation": validation})


@router.post("/compare", response_model=ArchitectureComparisonResponse)
async def compare_proposal(payload: ArchitectureComparisonRequest, request: Request, principal: ChatUser) -> ArchitectureComparisonResponse:
    return _service(request).compare(payload)


@router.get("/proposals/{proposal_id}/graph", response_model=ArchitectureGraphResponse)
async def get_graph(proposal_id: uuid.UUID, request: Request, session: SessionDep, principal: ChatUser) -> ArchitectureGraphResponse:
    return await _service(request).graph(session, principal, proposal_id)
