"""Permission and BTP scope evaluation. Default deny.

A request is allowed only when the user holds the permission through an *active* grant
(not revoked, not expired) whose scope covers the resource:

- a grant covers exactly the account or environment it names;
- it also covers descendants (subaccounts of a global account, environments of a
  subaccount) only when ``include_descendants`` is set on that grant;
- a grant never covers a parent: environment access does not reveal its subaccount;
- ``platform`` scope applies to administration permissions only, never to BTP data.

Grants are loaded from the database on every request, so revocation and expiry take
effect immediately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import (
    ADMIN_PERMISSIONS,
    ROLE_PERMISSIONS,
    Permission,
    RoleName,
    ScopeType,
)
from app.models import (
    AccessGrant,
    AccountType,
    BTPAccount,
    BTPEnvironment,
    KnowledgeScope,
    User,
)


@dataclass(frozen=True)
class Grant:
    id: uuid.UUID
    role: RoleName
    scope_type: ScopeType
    account_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    include_descendants: bool
    expires_at: datetime | None

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]


@dataclass
class Principal:
    user: User
    session_id: uuid.UUID
    grants: tuple[Grant, ...]
    # Captured as a plain value: a rollback expires ``user``, and reading an expired
    # attribute would need lazy IO, which async SQLAlchemy forbids.
    user_id: uuid.UUID = field(init=False)
    # Per-request memo of coverage by permission.
    _coverage: dict[Permission, Coverage] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.user_id = self.user.id

    @property
    def permissions(self) -> frozenset[Permission]:
        return frozenset(p for grant in self.grants for p in grant.permissions)

    def has_permission(self, permission: Permission) -> bool:
        """True if any active grant carries the permission, at any scope."""
        return permission in self.permissions

    def grants_with(self, permission: Permission) -> list[Grant]:
        return [grant for grant in self.grants if permission in grant.permissions]

    def is_platform_admin(self) -> bool:
        return any(
            g.role is RoleName.ADMINISTRATOR and g.scope_type is ScopeType.PLATFORM
            for g in self.grants
        )


@dataclass(frozen=True)
class Coverage:
    """The resources a permission reaches for one principal."""

    platform: bool
    account_ids: frozenset[uuid.UUID]
    environment_ids: frozenset[uuid.UUID]

    @property
    def empty(self) -> bool:
        return not (self.platform or self.account_ids or self.environment_ids)

    def covers_account(self, account_id: uuid.UUID) -> bool:
        return self.platform or account_id in self.account_ids

    def covers_environment(self, environment_id: uuid.UUID) -> bool:
        return self.platform or environment_id in self.environment_ids


EMPTY_COVERAGE = Coverage(False, frozenset(), frozenset())


async def load_active_grants(session: AsyncSession, user_id: uuid.UUID) -> tuple[Grant, ...]:
    now = datetime.now(UTC)
    rows = await session.scalars(
        select(AccessGrant).where(
            AccessGrant.user_id == user_id,
            AccessGrant.revoked_at.is_(None),
            (AccessGrant.expires_at.is_(None)) | (AccessGrant.expires_at > now),
        )
    )
    return tuple(
        Grant(
            id=row.id,
            role=RoleName(row.role_name),
            scope_type=ScopeType(row.scope_type),
            account_id=row.btp_account_id,
            environment_id=row.btp_environment_id,
            include_descendants=row.include_descendants,
            expires_at=row.expires_at,
        )
        for row in rows
    )


async def coverage_for(
    session: AsyncSession, principal: Principal, permission: Permission
) -> Coverage:
    if permission in principal._coverage:
        return principal._coverage[permission]
    coverage = await compute_coverage(session, principal.grants_with(permission), permission)
    principal._coverage[permission] = coverage
    return coverage


async def compute_coverage(
    session: AsyncSession, grants: list[Grant], permission: Permission
) -> Coverage:
    if not grants:
        return EMPTY_COVERAGE
    # Platform scope is meaningful for administration only.
    platform = permission in ADMIN_PERMISSIONS and any(
        g.scope_type is ScopeType.PLATFORM for g in grants
    )
    if platform:
        return Coverage(True, frozenset(), frozenset())

    accounts = {g.account_id for g in grants if g.account_id is not None}
    environments = {g.environment_id for g in grants if g.environment_id is not None}
    expanding = {g.account_id for g in grants if g.account_id and g.include_descendants}

    if expanding:
        # Subaccounts under expanding global accounts inherit the expansion.
        children = set(
            await session.scalars(
                select(BTPAccount.id).where(
                    BTPAccount.parent_account_id.in_(expanding),
                    BTPAccount.account_type == AccountType.SUBACCOUNT,
                )
            )
        )
        accounts |= children
        expanding |= children
        environments |= set(
            await session.scalars(
                select(BTPEnvironment.id).where(BTPEnvironment.btp_account_id.in_(expanding))
            )
        )
    return Coverage(False, frozenset(accounts), frozenset(environments))



@dataclass(frozen=True)
class KnowledgeCoverage:
    """Which knowledge sources a knowledge permission reaches for one principal.

    ``global_scope`` comes only from platform-scope grants of a role carrying the
    permission (Knowledge Reader/Manager); it never extends to BTP accounts. BTP-scoped
    knowledge follows the normal coverage rules (exact scope, descendants only on opt-in).
    """

    global_scope: bool
    btp: Coverage

    @property
    def empty(self) -> bool:
        return not (self.global_scope or self.btp.account_ids or self.btp.environment_ids)

    def covers(
        self,
        scope_type: str,
        btp_account_id: uuid.UUID | None,
        btp_environment_id: uuid.UUID | None,
    ) -> bool:
        if scope_type == KnowledgeScope.GLOBAL:
            return self.global_scope
        if scope_type == KnowledgeScope.BTP_ACCOUNT:
            return btp_account_id in self.btp.account_ids
        if scope_type == KnowledgeScope.BTP_ENVIRONMENT:
            return btp_environment_id in self.btp.environment_ids
        return False


async def knowledge_coverage_for(
    session: AsyncSession, principal: Principal, permission: Permission
) -> KnowledgeCoverage:
    global_scope = any(g.scope_type is ScopeType.PLATFORM for g in principal.grants_with(permission))
    # Knowledge permissions are not administration permissions, so platform grants add
    # nothing to BTP coverage here: platform means global knowledge, not every account.
    btp = await coverage_for(session, principal, permission)
    return KnowledgeCoverage(global_scope=global_scope, btp=btp)
