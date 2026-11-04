"""Presentation of access grants with human-readable scope names."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Grant
from app.auth.permissions import RoleName, ScopeType
from app.models import AccessGrant, BTPAccount, BTPEnvironment
from app.schemas.auth import GrantRead


async def _names(session: AsyncSession, account_ids: set, environment_ids: set) -> dict:
    names: dict = {}
    if account_ids:
        rows = await session.execute(
            select(BTPAccount.id, BTPAccount.name).where(BTPAccount.id.in_(account_ids))
        )
        names.update(dict(rows.all()))
    if environment_ids:
        rows = await session.execute(
            select(BTPEnvironment.id, BTPEnvironment.name).where(
                BTPEnvironment.id.in_(environment_ids)
            )
        )
        names.update(dict(rows.all()))
    return names


async def describe_rows(session: AsyncSession, rows: Iterable[AccessGrant]) -> list[GrantRead]:
    rows = list(rows)
    names = await _names(
        session,
        {r.btp_account_id for r in rows if r.btp_account_id},
        {r.btp_environment_id for r in rows if r.btp_environment_id},
    )
    return [
        GrantRead(
            id=r.id,
            role=RoleName(r.role_name),
            scope_type=ScopeType(r.scope_type),
            scope_id=r.scope_id,
            scope_name="Platform" if r.scope_type == ScopeType.PLATFORM else names.get(r.scope_id),
            include_descendants=r.include_descendants,
            expires_at=r.expires_at,
            created_at=r.created_at,
            revoked_at=r.revoked_at,
        )
        for r in rows
    ]


async def describe_grants(session: AsyncSession, grants: Iterable[Grant]) -> list[GrantRead]:
    grants = list(grants)
    names = await _names(
        session,
        {g.account_id for g in grants if g.account_id},
        {g.environment_id for g in grants if g.environment_id},
    )
    return [
        GrantRead(
            id=g.id,
            role=g.role,
            scope_type=g.scope_type,
            scope_id=g.account_id or g.environment_id,
            scope_name="Platform"
            if g.scope_type is ScopeType.PLATFORM
            else names.get(g.account_id or g.environment_id),
            include_descendants=g.include_descendants,
            expires_at=g.expires_at,
        )
        for g in grants
    ]
