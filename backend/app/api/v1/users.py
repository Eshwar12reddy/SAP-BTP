"""The current user's effective permissions and BTP scopes."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.auth.authorization import coverage_for
from app.auth.dependencies import CurrentPrincipal, SessionDep
from app.auth.permissions import Permission
from app.models import BTPAccount, BTPEnvironment
from app.schemas.auth import (
    BTPReadScopes,
    EffectivePermission,
    MyScopesResponse,
    PermissionScope,
    ScopeAccount,
    ScopeEnvironment,
)
from app.services.grant_view import describe_grants

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me/permissions", response_model=list[EffectivePermission])
async def my_permissions(principal: CurrentPrincipal) -> list[EffectivePermission]:
    """Each permission the user holds, with the grant scopes it comes from."""
    result = []
    for permission in Permission:
        grants = principal.grants_with(permission)
        if grants:
            result.append(
                EffectivePermission(
                    permission=permission.value,
                    scopes=[
                        PermissionScope(
                            scope_type=g.scope_type,
                            scope_id=g.account_id or g.environment_id,
                            include_descendants=g.include_descendants,
                        )
                        for g in grants
                    ],
                )
            )
    return result


@router.get("/me/scopes", response_model=MyScopesResponse)
async def my_scopes(session: SessionDep, principal: CurrentPrincipal) -> MyScopesResponse:
    """Grants, plus every account and environment the user can currently read
    (expanded through ``include_descendants``). Feeds the context selector."""
    coverage = await coverage_for(session, principal, Permission.BTP_READ)
    accounts = (
        list(
            await session.scalars(
                select(BTPAccount)
                .where(BTPAccount.id.in_(coverage.account_ids))
                .order_by(BTPAccount.name)
            )
        )
        if coverage.account_ids
        else []
    )
    environments = (
        list(
            await session.scalars(
                select(BTPEnvironment)
                .where(BTPEnvironment.id.in_(coverage.environment_ids))
                .order_by(BTPEnvironment.name)
            )
        )
        if coverage.environment_ids
        else []
    )
    return MyScopesResponse(
        grants=await describe_grants(session, principal.grants),
        btp_read=BTPReadScopes(
            accounts=[
                ScopeAccount(id=a.id, name=a.name, account_type=a.account_type) for a in accounts
            ],
            environments=[
                ScopeEnvironment(
                    id=e.id,
                    name=e.name,
                    runtime_type=e.runtime_type,
                    btp_account_id=e.btp_account_id
                    if coverage.covers_account(e.btp_account_id)
                    else None,
                )
                for e in environments
            ],
        ),
    )
