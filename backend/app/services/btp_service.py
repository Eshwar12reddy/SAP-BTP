"""Scope-filtered reads of the configured BTP hierarchy.

Filtering by the caller's coverage happens in SQL *before* LIMIT/OFFSET, so pages never
contain (or count) resources the caller cannot see. Resources outside the caller's scope
are reported as not found, so their existence cannot be probed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Coverage
from app.core.exceptions import NotFoundError
from app.models import BTPAccount, BTPEnvironment
from app.schemas.btp import BTPAccountRead, BTPEnvironmentRead

ACCOUNT_NOT_FOUND = "BTP account not found."
ENVIRONMENT_NOT_FOUND = "BTP environment not found."


def account_read(account: BTPAccount, coverage: Coverage) -> BTPAccountRead:
    parent = account.parent_account_id
    return BTPAccountRead(
        id=account.id,
        external_account_id=account.external_account_id,
        name=account.name,
        account_type=account.account_type,
        region=account.region,
        parent_account_id=parent if parent and coverage.covers_account(parent) else None,
        created_at=account.created_at,
    )


async def list_accounts(
    session: AsyncSession, coverage: Coverage, *, limit: int, offset: int
) -> tuple[list[BTPAccountRead], bool]:
    if not coverage.account_ids:
        return [], False
    rows = list(
        await session.scalars(
            select(BTPAccount)
            .where(BTPAccount.id.in_(coverage.account_ids))
            .order_by(BTPAccount.name, BTPAccount.id)
            .limit(limit + 1)
            .offset(offset)
        )
    )
    return [account_read(a, coverage) for a in rows[:limit]], len(rows) > limit


async def get_account(
    session: AsyncSession, coverage: Coverage, account_id: uuid.UUID
) -> BTPAccount | None:
    """The account if it exists *and* is covered; otherwise None."""
    if not coverage.covers_account(account_id):
        return None
    return await session.get(BTPAccount, account_id)


async def list_environments(
    session: AsyncSession,
    coverage: Coverage,
    account_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[BTPEnvironmentRead], bool]:
    rows = list(
        await session.scalars(
            select(BTPEnvironment)
            .where(
                BTPEnvironment.btp_account_id == account_id,
                BTPEnvironment.id.in_(coverage.environment_ids),
            )
            .order_by(BTPEnvironment.name, BTPEnvironment.id)
            .limit(limit + 1)
            .offset(offset)
        )
    )
    return [BTPEnvironmentRead.model_validate(e, from_attributes=True) for e in rows[:limit]], (
        len(rows) > limit
    )


@dataclass(frozen=True)
class CatalogEnvironment:
    environment: BTPEnvironment
    # Both None unless the caller can also read the parent subaccount.
    account_id: uuid.UUID | None
    account_name: str | None


async def authorized_catalog(
    session: AsyncSession, coverage: Coverage, *, limit: int = 200
) -> tuple[list[BTPAccount], list[CatalogEnvironment]]:
    """Every account and environment the coverage reaches, and nothing else."""
    accounts: list[BTPAccount] = []
    if coverage.account_ids:
        accounts = list(
            await session.scalars(
                select(BTPAccount)
                .where(BTPAccount.id.in_(coverage.account_ids))
                .order_by(BTPAccount.name, BTPAccount.id)
                .limit(limit)
            )
        )
    environments: list[CatalogEnvironment] = []
    if coverage.environment_ids:
        rows = await session.execute(
            select(BTPEnvironment, BTPAccount.id, BTPAccount.name)
            .join(BTPAccount, BTPAccount.id == BTPEnvironment.btp_account_id)
            .where(BTPEnvironment.id.in_(coverage.environment_ids))
            .order_by(BTPEnvironment.name, BTPAccount.name, BTPEnvironment.id)
            .limit(limit)
        )
        environments = [
            CatalogEnvironment(env, account_id, name)
            if coverage.covers_account(account_id)
            else CatalogEnvironment(env, None, None)
            for env, account_id, name in rows
        ]
    return accounts, environments


async def get_environment(
    session: AsyncSession, coverage: Coverage, environment_id: uuid.UUID
) -> BTPEnvironment:
    environment = (
        await session.get(BTPEnvironment, environment_id)
        if coverage.covers_environment(environment_id)
        else None
    )
    if environment is None:
        raise NotFoundError(ENVIRONMENT_NOT_FOUND)
    return environment
