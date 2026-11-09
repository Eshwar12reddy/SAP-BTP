"""Administration endpoints. Each endpoint requires an administration permission; the
service layer then applies delegated scope and anti-escalation rules."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.auth.authorization import Principal
from app.auth.dependencies import SessionDep, require_permission
from app.auth.permissions import Permission
from app.schemas.admin import (
    AdminUserDetail,
    AdminUserPage,
    AdminUserSummary,
    AuditEventPage,
    AuditEventRead,
    BTPAccountCreate,
    BTPAccountUpdate,
    BTPEnvironmentCreate,
    BTPEnvironmentUpdate,
    BTPHierarchyRead,
    GrantCreate,
    ManageableAccount,
    ManageableEnvironment,
    ManageableScopes,
    SessionsRevokedResponse,
    UserCreate,
    UserUpdate,
)
from app.schemas.auth import GrantRead, UserRead
from app.schemas.btp import BTPAccountRead, BTPEnvironmentRead
from app.services import admin_service
from app.services.admin_service import AdminContext
from app.services.grant_view import describe_rows

router = APIRouter(prefix="/admin", tags=["admin"])


def _context(permission: Permission):
    # Evaluated eagerly (no postponed annotations in this module) so the closure resolves.
    async def dependency(
        request: Request,
        session: SessionDep,
        principal: Annotated[Principal, Depends(require_permission(permission))],
    ) -> AdminContext:
        return AdminContext(session, request.app.state.session_factory, principal)

    return Depends(dependency)


def _account_read(account) -> BTPAccountRead:
    return BTPAccountRead.model_validate(account, from_attributes=True)


def _environment_read(environment) -> BTPEnvironmentRead:
    return BTPEnvironmentRead.model_validate(environment, from_attributes=True)


@router.get("/btp/scopes", response_model=BTPHierarchyRead)
async def btp_hierarchy(
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> BTPHierarchyRead:
    """Platform administrators' configured BTP hierarchy for provisioning and grants."""
    accounts, environments = await admin_service.list_btp_hierarchy(ctx)
    return BTPHierarchyRead(
        accounts=[_account_read(account) for account in accounts],
        environments=[_environment_read(environment) for environment in environments],
    )


@router.post("/btp/accounts", status_code=status.HTTP_201_CREATED, response_model=BTPAccountRead)
async def create_btp_account(
    payload: BTPAccountCreate,
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> BTPAccountRead:
    return _account_read(await admin_service.create_btp_account(ctx, payload))


@router.patch("/btp/accounts/{account_id}", response_model=BTPAccountRead)
async def update_btp_account(
    account_id: uuid.UUID,
    payload: BTPAccountUpdate,
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> BTPAccountRead:
    return _account_read(await admin_service.update_btp_account(ctx, account_id, payload))


@router.post(
    "/btp/accounts/{account_id}/environments",
    status_code=status.HTTP_201_CREATED,
    response_model=BTPEnvironmentRead,
)
async def create_btp_environment(
    account_id: uuid.UUID,
    payload: BTPEnvironmentCreate,
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> BTPEnvironmentRead:
    return _environment_read(await admin_service.create_btp_environment(ctx, account_id, payload))


@router.patch("/btp/environments/{environment_id}", response_model=BTPEnvironmentRead)
async def update_btp_environment(
    environment_id: uuid.UUID,
    payload: BTPEnvironmentUpdate,
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> BTPEnvironmentRead:
    return _environment_read(await admin_service.update_btp_environment(ctx, environment_id, payload))


@router.get("/users", response_model=AdminUserPage)
async def list_users(
    ctx: Annotated[AdminContext, _context(Permission.USERS_READ)],
    search: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> AdminUserPage:
    """Users the caller may manage (all for platform administrators)."""
    rows, has_more = await admin_service.list_users(ctx, search=search, limit=limit, offset=offset)
    return AdminUserPage(
        items=[
            AdminUserSummary(**UserRead.model_validate(u).model_dump(), roles=roles)
            for u, roles in rows
        ],
        has_more=has_more,
    )


@router.post("/users", status_code=status.HTTP_201_CREATED, response_model=UserRead)
async def create_user(
    payload: UserCreate, ctx: Annotated[AdminContext, _context(Permission.USERS_CREATE)]
) -> UserRead:
    """Create an account (platform administrators only). No self-registration exists."""
    return UserRead.model_validate(await admin_service.create_user(ctx, payload))


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: uuid.UUID, ctx: Annotated[AdminContext, _context(Permission.USERS_READ)]
) -> AdminUserDetail:
    user, grants, active_sessions, manageable = await admin_service.user_detail(ctx, user_id)
    return AdminUserDetail(
        user=UserRead.model_validate(user),
        grants=await describe_rows(ctx.session, grants),
        active_sessions=active_sessions,
        manageable_account=manageable,
    )


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    ctx: Annotated[AdminContext, _context(Permission.USERS_UPDATE)],
) -> UserRead:
    return UserRead.model_validate(await admin_service.update_user(ctx, user_id, payload))


@router.post("/users/{user_id}/disable", response_model=UserRead)
async def disable_user(
    user_id: uuid.UUID, ctx: Annotated[AdminContext, _context(Permission.USERS_DISABLE)]
) -> UserRead:
    """Disable an account and revoke all of its sessions immediately."""
    return UserRead.model_validate(await admin_service.set_user_active(ctx, user_id, active=False))


@router.post("/users/{user_id}/enable", response_model=UserRead)
async def enable_user(
    user_id: uuid.UUID, ctx: Annotated[AdminContext, _context(Permission.USERS_DISABLE)]
) -> UserRead:
    return UserRead.model_validate(await admin_service.set_user_active(ctx, user_id, active=True))


@router.post("/users/{user_id}/sessions/revoke", response_model=SessionsRevokedResponse)
async def revoke_sessions(
    user_id: uuid.UUID, ctx: Annotated[AdminContext, _context(Permission.SESSIONS_REVOKE)]
) -> SessionsRevokedResponse:
    count = await admin_service.revoke_user_sessions(ctx, user_id)
    return SessionsRevokedResponse(sessions_revoked=count)


@router.post(
    "/users/{user_id}/grants", status_code=status.HTTP_201_CREATED, response_model=GrantRead
)
async def assign_grant(
    user_id: uuid.UUID,
    payload: GrantCreate,
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> GrantRead:
    """Assign a role at a scope (this is how both roles and BTP scopes are granted)."""
    grant = await admin_service.assign_grant(ctx, user_id, payload)
    return (await describe_rows(ctx.session, [grant]))[0]


@router.post("/grants/{grant_id}/revoke", response_model=GrantRead)
async def revoke_grant(
    grant_id: uuid.UUID, ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)]
) -> GrantRead:
    """Revoke a grant. Takes effect on the user's very next request."""
    grant = await admin_service.revoke_grant(ctx, grant_id)
    return (await describe_rows(ctx.session, [grant]))[0]


@router.get("/scopes", response_model=ManageableScopes)
async def manageable_scopes(
    ctx: Annotated[AdminContext, _context(Permission.GRANTS_MANAGE)],
) -> ManageableScopes:
    """BTP accounts and environments the caller may assign (metadata only)."""
    platform, accounts, environments = await admin_service.manageable_scopes(ctx)
    return ManageableScopes(
        platform=platform,
        accounts=[
            ManageableAccount(
                id=a.id, name=a.name, account_type=a.account_type, parent_account_id=a.parent_account_id
            )
            for a in accounts
        ],
        environments=[
            ManageableEnvironment(
                id=e.id, name=e.name, runtime_type=e.runtime_type, btp_account_id=e.btp_account_id
            )
            for e in environments
        ],
    )


@router.get("/audit-events", response_model=AuditEventPage)
async def audit_events(
    ctx: Annotated[AdminContext, _context(Permission.AUDIT_READ)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    event_type: Annotated[str | None, Query(max_length=64)] = None,
    user_id: uuid.UUID | None = None,
) -> AuditEventPage:
    """Audit events, newest first. Delegated administrators see events in their scope."""
    rows, has_more = await admin_service.list_audit_events(
        ctx, limit=limit, offset=offset, event_type=event_type, user_id=user_id
    )
    return AuditEventPage(items=[AuditEventRead.model_validate(r) for r in rows], has_more=has_more)
