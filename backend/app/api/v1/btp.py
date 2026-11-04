"""BTP discovery over the configured account mapping. Requires ``btp:read`` on the
specific resource; anything outside the caller's scope answers 404."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.auth.authorization import Principal, coverage_for
from app.auth.dependencies import SessionDep, deny, require_permission
from app.auth.permissions import Permission
from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.integrations.sap_btp.provider import BTPIntegrationUnavailable
from app.schemas.btp import (
    BTPAccountPage,
    BTPAccountRead,
    BTPApplicationPage,
    BTPApplicationRead,
    BTPEnvironmentPage,
)
from app.services import btp_service

router = APIRouter(prefix="/btp", tags=["btp"])

Reader = Annotated[Principal, Depends(require_permission(Permission.BTP_READ))]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10_000)]


async def _deny_not_found(request: Request, principal: Principal, message: str, **scope) -> None:
    await deny(
        request,
        principal,
        reason="btp_scope_not_covered",
        permission=Permission.BTP_READ,
        error=NotFoundError(message),
        **scope,
    )


@router.get("/accounts", response_model=BTPAccountPage)
async def list_accounts(
    session: SessionDep, principal: Reader, limit: Limit = 50, offset: Offset = 0
) -> BTPAccountPage:
    """Accounts the caller can read. Environment-only access does not list the account."""
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    items, has_more = await btp_service.list_accounts(session, coverage, limit=limit, offset=offset)
    return BTPAccountPage(items=items, has_more=has_more)


@router.get("/accounts/{account_id}", response_model=BTPAccountRead)
async def get_account(
    account_id: uuid.UUID, request: Request, session: SessionDep, principal: Reader
) -> BTPAccountRead:
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    account = await btp_service.get_account(session, coverage, account_id)
    if account is None:
        await _deny_not_found(request, principal, btp_service.ACCOUNT_NOT_FOUND)
    return btp_service.account_read(account, coverage)  # type: ignore[arg-type]


@router.get("/accounts/{account_id}/environments", response_model=BTPEnvironmentPage)
async def list_environments(
    account_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Reader,
    limit: Limit = 50,
    offset: Offset = 0,
) -> BTPEnvironmentPage:
    """Environments of an account the caller can read, filtered to covered environments."""
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    if await btp_service.get_account(session, coverage, account_id) is None:
        await _deny_not_found(request, principal, btp_service.ACCOUNT_NOT_FOUND)
    items, has_more = await btp_service.list_environments(
        session, coverage, account_id, limit=limit, offset=offset
    )
    return BTPEnvironmentPage(items=items, has_more=has_more)


@router.get(
    "/environments/{environment_id}/applications",
    response_model=BTPApplicationPage,
    responses={503: {"description": "Live SAP BTP integration not configured"}},
)
async def list_applications(
    environment_id: uuid.UUID, request: Request, session: SessionDep, principal: Reader
) -> BTPApplicationPage:
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    if not coverage.covers_environment(environment_id):
        await _deny_not_found(request, principal, btp_service.ENVIRONMENT_NOT_FOUND)
    environment = await btp_service.get_environment(session, coverage, environment_id)
    provider = request.app.state.btp_provider
    try:
        applications = await provider.list_applications(environment)
    except BTPIntegrationUnavailable:
        raise ServiceUnavailableError(
            "Live SAP BTP integration is not configured, so applications cannot be listed.",
            code="btp_integration_unavailable",
        ) from None
    return BTPApplicationPage(
        items=[BTPApplicationRead(id=a.id, name=a.name, state=a.state) for a in applications],
        source=provider.name,
    )
