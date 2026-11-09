"""User and access administration with delegated scope.

Rules (all enforced server-side):
- Platform administrators (administrator role at ``platform`` scope) manage every user.
- Delegated administrators (administrator role on a BTP account) only see users who hold
  an active grant inside their coverage, and only assign/revoke non-administrator grants
  inside it. Account-wide actions (create, update, disable, revoke sessions) are
  platform-only, because they affect the user everywhere.
- Only platform administrators assign or revoke the administrator role.
- Nobody changes their own grants, status or sessions through these endpoints.
- Every change commits together with its audit event; every denial is audited.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import passwords
from app.auth.authorization import Coverage, Principal, coverage_for
from app.auth.permissions import ROLE_SCOPE_TYPES, Permission, RoleName, ScopeType
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import (
    AccessGrant,
    AccountType,
    AuditEvent,
    BTPAccount,
    BTPEnvironment,
    Role,
    RuntimeType,
    Session,
    User,
)
from app.schemas.admin import (
    BTPAccountCreate,
    BTPAccountUpdate,
    BTPEnvironmentCreate,
    BTPEnvironmentUpdate,
    GrantCreate,
    UserCreate,
    UserUpdate,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome
from app.services.auth_service import revoke_sessions

USER_NOT_FOUND = "User not found."
GRANT_NOT_FOUND = "Grant not found."
BTP_ACCOUNT_NOT_FOUND = "BTP account not found."
BTP_ENVIRONMENT_NOT_FOUND = "BTP environment not found."


async def list_btp_hierarchy(ctx: AdminContext) -> tuple[list[BTPAccount], list[BTPEnvironment]]:
    """Return the configured hierarchy to a platform administrator only."""
    await _require_platform(ctx, Permission.GRANTS_MANAGE)
    accounts = list(await ctx.session.scalars(select(BTPAccount).order_by(BTPAccount.name, BTPAccount.id)))
    environments = list(
        await ctx.session.scalars(
            select(BTPEnvironment).order_by(BTPEnvironment.name, BTPEnvironment.id)
        )
    )
    return accounts, environments


async def create_btp_account(ctx: AdminContext, data: BTPAccountCreate) -> BTPAccount:
    """Register one global account or subaccount; never assigns access automatically."""
    await _require_platform(ctx, Permission.GRANTS_MANAGE)
    if data.account_type not in {AccountType.GLOBAL_ACCOUNT.value, AccountType.SUBACCOUNT.value}:
        raise BadRequestError("account_type must be global_account or subaccount.", code="invalid_account_type")
    if data.account_type == AccountType.GLOBAL_ACCOUNT.value:
        if data.parent_account_id is not None:
            raise BadRequestError("Global accounts cannot have a parent account.", code="invalid_hierarchy")
        parent_type = None
    else:
        if data.parent_account_id is None:
            raise BadRequestError("Subaccounts require a global-account parent.", code="invalid_hierarchy")
        parent = await ctx.session.get(BTPAccount, data.parent_account_id)
        if parent is None or parent.account_type != AccountType.GLOBAL_ACCOUNT.value:
            raise NotFoundError(BTP_ACCOUNT_NOT_FOUND)
        parent_type = AccountType.GLOBAL_ACCOUNT.value
    if await ctx.session.scalar(select(BTPAccount.id).where(BTPAccount.external_account_id == data.external_account_id)):
        raise ConflictError("A BTP account with this external id already exists.", code="btp_account_exists")
    row = BTPAccount(
        id=uuid.uuid4(),
        external_account_id=data.external_account_id,
        name=data.name,
        account_type=data.account_type,
        region=data.region or None,
        parent_account_id=data.parent_account_id,
        parent_account_type=parent_type,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    audit_service.record(
        ctx.session,
        AuditEventType.BTP_MAPPING_CHANGED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        btp_account_id=row.id,
        details={"via": "admin_api", "action": "account_created", "account_type": row.account_type},
    )
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ConflictError("A BTP account with this external id already exists.", code="btp_account_exists") from None
    return row


async def update_btp_account(ctx: AdminContext, account_id: uuid.UUID, data: BTPAccountUpdate) -> BTPAccount:
    await _require_platform(ctx, Permission.GRANTS_MANAGE)
    row = await ctx.session.get(BTPAccount, account_id)
    if row is None:
        raise NotFoundError(BTP_ACCOUNT_NOT_FOUND)
    duplicate = await ctx.session.scalar(
        select(BTPAccount.id).where(
            BTPAccount.external_account_id == data.external_account_id,
            BTPAccount.id != account_id,
        )
    )
    if duplicate:
        raise ConflictError("A BTP account with this external id already exists.", code="btp_account_exists")
    row.external_account_id = data.external_account_id
    row.name = data.name
    row.region = data.region or None
    audit_service.record(
        ctx.session,
        AuditEventType.BTP_MAPPING_CHANGED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        btp_account_id=row.id,
        details={"via": "admin_api", "action": "account_updated"},
    )
    await ctx.session.commit()
    return row


async def create_btp_environment(
    ctx: AdminContext, account_id: uuid.UUID, data: BTPEnvironmentCreate
) -> BTPEnvironment:
    await _require_platform(ctx, Permission.GRANTS_MANAGE)
    account = await ctx.session.get(BTPAccount, account_id)
    if account is None or account.account_type != AccountType.SUBACCOUNT.value:
        raise NotFoundError(BTP_ACCOUNT_NOT_FOUND)
    if data.runtime_type not in {runtime.value for runtime in RuntimeType}:
        raise BadRequestError("Unsupported runtime type.", code="invalid_runtime_type")
    same_name = await ctx.session.scalar(
        select(BTPEnvironment.id).where(BTPEnvironment.btp_account_id == account_id, BTPEnvironment.name == data.name)
    )
    if same_name:
        raise ConflictError("An environment with this name already exists in the subaccount.", code="btp_environment_exists")
    if data.external_runtime_id:
        same_external = await ctx.session.scalar(
            select(BTPEnvironment.id).where(
                BTPEnvironment.runtime_type == data.runtime_type,
                BTPEnvironment.external_runtime_id == data.external_runtime_id,
            )
        )
        if same_external:
            raise ConflictError("An environment with this runtime id already exists.", code="btp_environment_exists")
    row = BTPEnvironment(
        id=uuid.uuid4(),
        btp_account_id=account.id,
        btp_account_type=AccountType.SUBACCOUNT.value,
        name=data.name,
        runtime_type=data.runtime_type,
        external_runtime_id=data.external_runtime_id or None,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    audit_service.record(
        ctx.session,
        AuditEventType.BTP_MAPPING_CHANGED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        btp_account_id=account.id,
        btp_environment_id=row.id,
        details={"via": "admin_api", "action": "environment_created", "runtime_type": row.runtime_type},
    )
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ConflictError("An environment with this name or runtime id already exists.", code="btp_environment_exists") from None
    return row


async def update_btp_environment(
    ctx: AdminContext, environment_id: uuid.UUID, data: BTPEnvironmentUpdate
) -> BTPEnvironment:
    await _require_platform(ctx, Permission.GRANTS_MANAGE)
    row = await ctx.session.get(BTPEnvironment, environment_id)
    if row is None:
        raise NotFoundError(BTP_ENVIRONMENT_NOT_FOUND)
    if data.runtime_type not in {runtime.value for runtime in RuntimeType}:
        raise BadRequestError("Unsupported runtime type.", code="invalid_runtime_type")
    same_name = await ctx.session.scalar(
        select(BTPEnvironment.id).where(
            BTPEnvironment.btp_account_id == row.btp_account_id,
            BTPEnvironment.name == data.name,
            BTPEnvironment.id != environment_id,
        )
    )
    if same_name:
        raise ConflictError("An environment with this name already exists in the subaccount.", code="btp_environment_exists")
    if data.external_runtime_id:
        same_external = await ctx.session.scalar(
            select(BTPEnvironment.id).where(
                BTPEnvironment.runtime_type == data.runtime_type,
                BTPEnvironment.external_runtime_id == data.external_runtime_id,
                BTPEnvironment.id != environment_id,
            )
        )
        if same_external:
            raise ConflictError("An environment with this runtime id already exists.", code="btp_environment_exists")
    row.name = data.name
    row.runtime_type = data.runtime_type
    row.external_runtime_id = data.external_runtime_id or None
    audit_service.record(
        ctx.session,
        AuditEventType.BTP_MAPPING_CHANGED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        btp_account_id=row.btp_account_id,
        btp_environment_id=row.id,
        details={"via": "admin_api", "action": "environment_updated"},
    )
    await ctx.session.commit()
    return row


@dataclass
class AdminContext:
    session: AsyncSession
    session_factory: async_sessionmaker[AsyncSession]
    principal: Principal

    @property
    def actor_id(self) -> uuid.UUID:
        return self.principal.user_id

    async def coverage(self, permission: Permission) -> Coverage:
        return await coverage_for(self.session, self.principal, permission)

    async def deny(self, reason: str, error: Exception | None = None, **scope) -> None:
        await audit_service.record_standalone(
            self.session_factory,
            AuditEventType.ACCESS_DENIED,
            Outcome.DENIED,
            actor_user_id=self.actor_id,
            details={"reason": reason},
            **scope,
        )
        raise error or PermissionDeniedError()


def _active_grant_condition(now: datetime):
    return (
        AccessGrant.revoked_at.is_(None),
        or_(AccessGrant.expires_at.is_(None), AccessGrant.expires_at > now),
    )


def _grant_in_coverage(coverage: Coverage):
    conditions = []
    if coverage.account_ids:
        conditions.append(AccessGrant.btp_account_id.in_(coverage.account_ids))
    if coverage.environment_ids:
        conditions.append(AccessGrant.btp_environment_id.in_(coverage.environment_ids))
    return or_(*conditions) if conditions else None


def _visible_users_condition(coverage: Coverage, now: datetime):
    """SQL condition on User: visible to an administrator with this coverage."""
    if coverage.platform:
        return None
    in_scope = _grant_in_coverage(coverage)
    if in_scope is None:
        return False
    return exists().where(AccessGrant.user_id == User.id, *_active_grant_condition(now), in_scope)


async def _require_platform(ctx: AdminContext, permission: Permission, **scope) -> None:
    if not (await ctx.coverage(permission)).platform:
        await ctx.deny("platform_scope_required", **scope)


def _forbid_self(ctx: AdminContext, user_id: uuid.UUID) -> None:
    if user_id == ctx.actor_id:
        raise PermissionDeniedError(
            "You cannot change your own access or account status here.",
            code="self_modification_forbidden",
        )


async def _visible_user(ctx: AdminContext, user_id: uuid.UUID) -> User:
    now = datetime.now(UTC)
    condition = _visible_users_condition(await ctx.coverage(Permission.USERS_READ), now)
    query = select(User).where(User.id == user_id)
    if condition is False:
        user = None
    else:
        user = await ctx.session.scalar(query if condition is None else query.where(condition))
    if user is None:
        raise NotFoundError(USER_NOT_FOUND)
    return user


# ---------- users ----------


async def list_users(
    ctx: AdminContext, *, search: str | None, limit: int, offset: int
) -> tuple[list[tuple[User, list[RoleName]]], bool]:
    now = datetime.now(UTC)
    coverage = await ctx.coverage(Permission.USERS_READ)
    condition = _visible_users_condition(coverage, now)
    if condition is False:
        return [], False
    query = select(User)
    if condition is not None:
        query = query.where(condition)
    if search:
        pattern = "%" + search.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            or_(User.email.like(pattern, escape="\\"), func.lower(User.display_name).like(pattern, escape="\\"))
        )
    users = list(await ctx.session.scalars(query.order_by(User.email).limit(limit + 1).offset(offset)))
    has_more = len(users) > limit
    users = users[:limit]
    roles = await _roles_for(ctx.session, [u.id for u in users], coverage, now)
    return [(u, roles.get(u.id, [])) for u in users], has_more


async def _roles_for(
    session: AsyncSession, user_ids: list[uuid.UUID], coverage: Coverage, now: datetime
) -> dict[uuid.UUID, list[RoleName]]:
    if not user_ids:
        return {}
    query = select(AccessGrant.user_id, AccessGrant.role_name).where(
        AccessGrant.user_id.in_(user_ids), *_active_grant_condition(now)
    )
    if not coverage.platform:
        query = query.where(_grant_in_coverage(coverage))
    result: dict[uuid.UUID, set[RoleName]] = {}
    for user_id, role in (await session.execute(query)):
        result.setdefault(user_id, set()).add(RoleName(role))
    order = list(RoleName)
    return {k: sorted(v, key=order.index) for k, v in result.items()}


async def user_detail(ctx: AdminContext, user_id: uuid.UUID) -> tuple[User, list[AccessGrant], int, bool]:
    user = await _visible_user(ctx, user_id)
    coverage = await ctx.coverage(Permission.USERS_READ)
    query = select(AccessGrant).where(AccessGrant.user_id == user.id, AccessGrant.revoked_at.is_(None))
    if not coverage.platform:
        query = query.where(_grant_in_coverage(coverage))
    grants = list(await ctx.session.scalars(query.order_by(AccessGrant.created_at)))
    now = datetime.now(UTC)
    active_sessions = await ctx.session.scalar(
        select(func.count()).where(
            Session.user_id == user.id,
            Session.revoked_at.is_(None),
            Session.absolute_expires_at > now,
            Session.idle_expires_at > now,
        )
    )
    return user, grants, active_sessions or 0, coverage.platform


async def create_user(ctx: AdminContext, data: UserCreate) -> User:
    await _require_platform(ctx, Permission.USERS_CREATE)
    problems = passwords.password_policy_problems(data.password, email=data.email)
    if problems:
        raise BadRequestError("Password " + "; ".join(problems) + ".", code="weak_password")
    if await ctx.session.scalar(select(User.id).where(User.email == data.email)):
        raise ConflictError("A user with this email already exists.", code="email_taken")
    user = User(
        id=uuid.uuid4(),
        email=data.email,
        display_name=data.display_name,
        password_hash=await asyncio.to_thread(passwords.hash_password, data.password),
    )
    ctx.session.add(user)
    try:
        await ctx.session.flush()  # the audit row references the new user
    except IntegrityError:
        await ctx.session.rollback()
        raise ConflictError("A user with this email already exists.", code="email_taken") from None
    audit_service.record(
        ctx.session,
        AuditEventType.USER_CREATED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        target_user_id=user.id,
    )
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ConflictError("A user with this email already exists.", code="email_taken") from None
    return user


async def update_user(ctx: AdminContext, user_id: uuid.UUID, data: UserUpdate) -> User:
    await _require_platform(ctx, Permission.USERS_UPDATE)
    user = await _visible_user(ctx, user_id)
    user.display_name = data.display_name
    audit_service.record(
        ctx.session,
        AuditEventType.USER_UPDATED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        target_user_id=user.id,
        details={"fields": ["display_name"]},
    )
    await ctx.session.commit()
    return user


async def set_user_active(ctx: AdminContext, user_id: uuid.UUID, *, active: bool) -> User:
    await _require_platform(ctx, Permission.USERS_DISABLE)
    _forbid_self(ctx, user_id)
    user = await _visible_user(ctx, user_id)
    revoked = 0
    if user.is_active != active:
        user.is_active = active
        if not active:
            revoked = await revoke_sessions(ctx.session, user.id, reason="account_disabled")
        audit_service.record(
            ctx.session,
            AuditEventType.USER_ENABLED if active else AuditEventType.USER_DISABLED,
            Outcome.SUCCESS,
            actor_user_id=ctx.actor_id,
            target_user_id=user.id,
            details={"sessions_revoked": revoked} if not active else {},
        )
        await ctx.session.commit()
    return user


async def revoke_user_sessions(ctx: AdminContext, user_id: uuid.UUID) -> int:
    await _require_platform(ctx, Permission.SESSIONS_REVOKE)
    _forbid_self(ctx, user_id)
    user = await _visible_user(ctx, user_id)
    count = await revoke_sessions(ctx.session, user.id, reason="admin_revoked")
    audit_service.record(
        ctx.session,
        AuditEventType.SESSIONS_REVOKED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        target_user_id=user.id,
        details={"sessions_revoked": count},
    )
    await ctx.session.commit()
    return count


# ---------- grants ----------


def _scope_fields(scope_type: ScopeType, scope_id: uuid.UUID | None) -> dict:
    if scope_type is ScopeType.ENVIRONMENT:
        return {"scope_type": scope_type.value, "btp_environment_id": scope_id}
    if scope_type is ScopeType.PLATFORM:
        return {"scope_type": scope_type.value}
    return {"scope_type": scope_type.value, "btp_account_id": scope_id}


def _covers(coverage: Coverage, scope_type: ScopeType, scope_id: uuid.UUID | None) -> bool:
    if scope_type is ScopeType.PLATFORM:
        return coverage.platform
    if scope_type is ScopeType.ENVIRONMENT:
        return coverage.covers_environment(scope_id)  # type: ignore[arg-type]
    return coverage.covers_account(scope_id)  # type: ignore[arg-type]


async def assign_grant(ctx: AdminContext, user_id: uuid.UUID, data: GrantCreate) -> AccessGrant:
    _forbid_self(ctx, user_id)
    scope = _scope_fields(data.scope_type, data.scope_id)
    audit_scope = {k: v for k, v in scope.items() if k != "scope_type"} | {"scope_type": data.scope_type.value}

    # Shape validation (mirrors the database constraints with readable errors).
    if data.scope_type not in ROLE_SCOPE_TYPES[data.role]:
        raise BadRequestError(
            f"The {data.role.value} role cannot be granted at {data.scope_type.value} scope.",
            code="invalid_scope",
        )
    if (data.scope_type is ScopeType.PLATFORM) != (data.scope_id is None):
        raise BadRequestError("scope_id is required for BTP scopes and forbidden for platform.", code="invalid_scope")
    if data.include_descendants and data.scope_type not in (ScopeType.GLOBAL_ACCOUNT, ScopeType.SUBACCOUNT):
        raise BadRequestError("include_descendants applies to account scopes only.", code="invalid_scope")
    now = datetime.now(UTC)
    if data.expires_at is not None and data.expires_at <= now:
        raise BadRequestError("expires_at must be in the future.", code="invalid_expiry")

    # Privilege boundaries.
    coverage = await ctx.coverage(Permission.GRANTS_MANAGE)
    if data.role is RoleName.ADMINISTRATOR and not coverage.platform:
        await ctx.deny("administrator_grant_requires_platform", target_user_id=user_id, **audit_scope)
    target = await _visible_user(ctx, user_id)

    # The scope must exist and be of the declared type.
    if data.scope_type is ScopeType.ENVIRONMENT:
        found = await ctx.session.get(BTPEnvironment, data.scope_id)
    elif data.scope_type is not ScopeType.PLATFORM:
        account = await ctx.session.get(BTPAccount, data.scope_id)
        found = account if account and account.account_type == data.scope_type.value else None
    else:
        found = True
    if not found or not _covers(coverage, data.scope_type, data.scope_id):
        # Unknown and out-of-scope look the same to the caller.
        await ctx.deny(
            "scope_outside_delegation" if found else "scope_not_found",
            NotFoundError("Scope not found."),
            target_user_id=user_id,
            **({} if not found else audit_scope),
        )

    role = await ctx.session.scalar(select(Role).where(Role.name == data.role.value))
    assert role is not None  # seeded by migration

    account_id, environment_id = scope.get("btp_account_id"), scope.get("btp_environment_id")
    existing = await ctx.session.scalar(
        select(AccessGrant).where(
            AccessGrant.user_id == target.id,
            AccessGrant.role_id == role.id,
            AccessGrant.revoked_at.is_(None),
            AccessGrant.scope_type == data.scope_type.value,
            AccessGrant.btp_account_id.is_(None)
            if account_id is None
            else AccessGrant.btp_account_id == account_id,
            AccessGrant.btp_environment_id.is_(None)
            if environment_id is None
            else AccessGrant.btp_environment_id == environment_id,
        )
    )
    if existing is not None:
        if existing.expires_at is None or existing.expires_at > now:
            raise ConflictError("The user already has this role at this scope.", code="grant_exists")
        # An expired grant is superseded so the new one can satisfy the uniqueness rule.
        existing.revoked_at = now
        existing.revoked_by = ctx.actor_id
        await ctx.session.flush()

    grant = AccessGrant(
        id=uuid.uuid4(),
        user_id=target.id,
        role_id=role.id,
        role_name=role.name,
        include_descendants=data.include_descendants,
        granted_by=ctx.actor_id,
        expires_at=data.expires_at,
        **scope,
    )
    ctx.session.add(grant)
    audit_service.record(
        ctx.session,
        AuditEventType.GRANT_ASSIGNED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        target_user_id=target.id,
        details={
            "grant_id": str(grant.id),
            "role": role.name,
            "include_descendants": data.include_descendants,
            "expires_at": data.expires_at.isoformat() if data.expires_at else None,
        },
        **audit_scope,
    )
    try:
        await ctx.session.commit()
    except IntegrityError:
        await ctx.session.rollback()
        raise ConflictError("The user already has this role at this scope.", code="grant_exists") from None
    return grant


async def revoke_grant(ctx: AdminContext, grant_id: uuid.UUID) -> AccessGrant:
    grant = await ctx.session.scalar(
        select(AccessGrant).where(AccessGrant.id == grant_id).with_for_update()
    )
    if grant is None or grant.revoked_at is not None:
        raise NotFoundError(GRANT_NOT_FOUND)
    _forbid_self(ctx, grant.user_id)
    scope_type = ScopeType(grant.scope_type)
    audit_scope = {
        "scope_type": grant.scope_type,
        "btp_account_id": grant.btp_account_id,
        "btp_environment_id": grant.btp_environment_id,
    }
    coverage = await ctx.coverage(Permission.GRANTS_MANAGE)
    allowed = _covers(coverage, scope_type, grant.scope_id) and (
        grant.role_name != RoleName.ADMINISTRATOR or coverage.platform
    )
    if not allowed:
        target_user_id = grant.user_id
        await ctx.session.rollback()
        await ctx.deny(
            "grant_outside_delegation",
            NotFoundError(GRANT_NOT_FOUND),
            target_user_id=target_user_id,
            **audit_scope,
        )
    grant.revoked_at = datetime.now(UTC)
    grant.revoked_by = ctx.actor_id
    audit_service.record(
        ctx.session,
        AuditEventType.GRANT_REVOKED,
        Outcome.SUCCESS,
        actor_user_id=ctx.actor_id,
        target_user_id=grant.user_id,
        details={"grant_id": str(grant.id), "role": grant.role_name},
        **audit_scope,
    )
    await ctx.session.commit()
    return grant


# ---------- audit and scopes ----------


async def list_audit_events(
    ctx: AdminContext,
    *,
    limit: int,
    offset: int,
    event_type: str | None,
    user_id: uuid.UUID | None,
) -> tuple[list[AuditEvent], bool]:
    coverage = await ctx.coverage(Permission.AUDIT_READ)
    query = select(AuditEvent)
    if not coverage.platform:
        conditions = []
        if coverage.account_ids:
            conditions.append(AuditEvent.btp_account_id.in_(coverage.account_ids))
        if coverage.environment_ids:
            conditions.append(AuditEvent.btp_environment_id.in_(coverage.environment_ids))
        if not conditions:
            return [], False
        query = query.where(or_(*conditions))
    if event_type:
        query = query.where(AuditEvent.event_type == event_type)
    if user_id:
        query = query.where(or_(AuditEvent.actor_user_id == user_id, AuditEvent.target_user_id == user_id))
    rows = list(
        await ctx.session.scalars(
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1).offset(offset)
        )
    )
    return rows[:limit], len(rows) > limit


async def manageable_scopes(
    ctx: AdminContext,
) -> tuple[bool, list[BTPAccount], list[BTPEnvironment]]:
    coverage = await ctx.coverage(Permission.GRANTS_MANAGE)
    accounts = select(BTPAccount).order_by(BTPAccount.account_type, BTPAccount.name)
    environments = select(BTPEnvironment).order_by(BTPEnvironment.name)
    if not coverage.platform:
        accounts = accounts.where(BTPAccount.id.in_(coverage.account_ids))
        environments = environments.where(BTPEnvironment.id.in_(coverage.environment_ids))
    return (
        coverage.platform,
        list(await ctx.session.scalars(accounts)),
        list(await ctx.session.scalars(environments)),
    )
