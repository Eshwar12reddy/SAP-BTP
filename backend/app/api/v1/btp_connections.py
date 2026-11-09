"""Secure BTP connection metadata and active-environment selection endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.core.exceptions import NotFoundError
from app.schemas.btp_connections import (
    ActiveEnvironmentRead,
    ActiveEnvironmentUpdate,
    AvailableEnvironmentPage,
    BTPConnectionCreate,
    BTPConnectionPage,
    BTPConnectionRead,
    BTPConnectionUpdate,
    BTPConnectionValidationRead,
    BTPConnectionValidationRequest,
)
from app.services import audit_service, btp_connection_service
from app.services.audit_service import AuditEventType, Outcome

router = APIRouter(prefix="/btp", tags=["btp-connections"])
Reader = Annotated[Principal, Depends(require_permission(Permission.BTP_CONNECTIONS_READ))]
Creator = Annotated[Principal, Depends(require_permission(Permission.BTP_CONNECTIONS_CREATE))]
Updater = Annotated[Principal, Depends(require_permission(Permission.BTP_CONNECTIONS_UPDATE))]
Validator = Annotated[Principal, Depends(require_permission(Permission.BTP_CONNECTIONS_VALIDATE))]
Selector = Annotated[Principal, Depends(require_permission(Permission.BTP_ENVIRONMENT_SELECT))]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=10_000)]


async def _audit_denied(
    request: Request,
    principal: Principal,
    *,
    connection_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
) -> None:
    await audit_service.record_standalone(
        request.app.state.session_factory,
        AuditEventType.BTP_CONNECTION_ACCESS_DENIED,
        Outcome.DENIED,
        actor_user_id=principal.user_id,
        details={
            "connection_id": str(connection_id) if connection_id else None,
            "environment_id": str(environment_id) if environment_id else None,
            "reason": "not_found_or_out_of_scope",
        },
    )


@router.post(
    "/connections",
    status_code=status.HTTP_201_CREATED,
    response_model=BTPConnectionRead,
)
async def create_connection(
    payload: BTPConnectionCreate,
    request: Request,
    session: SessionDep,
    principal: Creator,
) -> BTPConnectionRead:
    try:
        return await btp_connection_service.create_connection(
            session, principal, request.app.state.settings, payload
        )
    except NotFoundError:
        await _audit_denied(request, principal, environment_id=payload.btp_environment_id)
        raise


@router.get("/connections", response_model=BTPConnectionPage)
async def list_connections(
    request: Request,
    session: SessionDep,
    principal: Reader,
    limit: Limit = 50,
    offset: Offset = 0,
) -> BTPConnectionPage:
    items, has_more = await btp_connection_service.list_connections(
        session, principal, request.app.state.settings, limit=limit, offset=offset
    )
    return BTPConnectionPage(items=items, has_more=has_more)


@router.get("/connections/{connection_id}", response_model=BTPConnectionRead)
async def get_connection(
    connection_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Reader,
) -> BTPConnectionRead:
    try:
        return await btp_connection_service.get_connection(
            session, principal, request.app.state.settings, connection_id
        )
    except NotFoundError:
        await _audit_denied(request, principal, connection_id=connection_id)
        raise


@router.patch("/connections/{connection_id}", response_model=BTPConnectionRead)
async def update_connection(
    connection_id: uuid.UUID,
    payload: BTPConnectionUpdate,
    request: Request,
    session: SessionDep,
    principal: Updater,
) -> BTPConnectionRead:
    try:
        return await btp_connection_service.update_connection(
            session, principal, request.app.state.settings, connection_id, payload
        )
    except NotFoundError:
        await _audit_denied(request, principal, connection_id=connection_id)
        raise


@router.post("/connections/{connection_id}/disable", response_model=BTPConnectionRead)
async def disable_connection(
    connection_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: Updater,
) -> BTPConnectionRead:
    try:
        return await btp_connection_service.disable_connection(
            session, principal, request.app.state.settings, connection_id
        )
    except NotFoundError:
        await _audit_denied(request, principal, connection_id=connection_id)
        raise


@router.post(
    "/connections/{connection_id}/validate",
    response_model=BTPConnectionValidationRead,
    responses={503: {"description": "Live validation is disabled or unavailable"}},
)
async def validate_connection(
    connection_id: uuid.UUID,
    payload: BTPConnectionValidationRequest,
    request: Request,
    session: SessionDep,
    principal: Validator,
) -> BTPConnectionValidationRead:
    try:
        connection, outcome = await btp_connection_service.validate_connection(
            session,
            principal,
            request.app.state.settings,
            request.app.state.session_factory,
            connection_id,
            live=payload.live,
        )
    except NotFoundError:
        await _audit_denied(request, principal, connection_id=connection_id)
        raise
    return BTPConnectionValidationRead(
        connection=connection,
        result=outcome.result,
        error_category=outcome.error_category,
        live_check_performed=outcome.live_check_performed,
    )


@router.get("/environments/available", response_model=AvailableEnvironmentPage)
async def available_environments(
    request: Request,
    session: SessionDep,
    principal: Selector,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AvailableEnvironmentPage:
    items, has_more = await btp_connection_service.available_environments(
        session, principal, request.app.state.settings, limit=limit, offset=offset
    )
    return AvailableEnvironmentPage(items=items, has_more=has_more)


@router.put("/environments/active", response_model=ActiveEnvironmentRead)
async def select_active_environment(
    payload: ActiveEnvironmentUpdate,
    request: Request,
    session: SessionDep,
    principal: Selector,
) -> ActiveEnvironmentRead:
    try:
        connection = await btp_connection_service.select_active_connection(
            session,
            principal,
            payload.connection_id,
            request.app.state.session_factory,
            request.app.state.settings,
        )
    except NotFoundError:
        await _audit_denied(request, principal, connection_id=payload.connection_id)
        raise
    return ActiveEnvironmentRead(connection=connection)


@router.get("/environments/active", response_model=ActiveEnvironmentRead)
async def get_active_environment(
    request: Request, session: SessionDep, principal: Selector
) -> ActiveEnvironmentRead:
    return ActiveEnvironmentRead(
        connection=await btp_connection_service.active_connection(
            session, principal, request.app.state.settings
        )
    )
