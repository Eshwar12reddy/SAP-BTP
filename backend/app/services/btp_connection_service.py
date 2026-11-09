"""Authorized SAP BTP connection metadata, validation and active selection."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.authorization import Coverage, Principal, coverage_for
from app.auth.permissions import Permission
from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.integrations.sap_btp.credentials import CredentialResolver
from app.integrations.sap_btp.validation import (
    ValidationOutcome,
    validate_configuration,
    validate_live,
)
from app.models import BTPEnvironment
from app.models.btp_connection import (
    BTPActiveConnection,
    BTPConnection,
    ConnectionValidationResult,
)
from app.schemas.btp_connections import (
    AvailableEnvironmentRead,
    BTPConnectionCreate,
    BTPConnectionRead,
    BTPConnectionUpdate,
)
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

CONNECTION_NOT_FOUND = "BTP connection not found."
ENVIRONMENT_NOT_FOUND = "BTP environment not found."


@dataclass
class BTPConnectionContext:
    """Trusted Phase 12 handoff; construct only after current authorization checks."""

    connection_id: uuid.UUID
    runtime_type: str
    btp_account_id: uuid.UUID
    btp_environment_id: uuid.UUID
    api_endpoint: str
    credential_reference: str
    authentication_method: str
    credential_resolver: CredentialResolver
    timeout_seconds: float
    max_retries: int

    def resolve_credentials(self):
        return self.credential_resolver.resolve(self.credential_reference)


def _scope(environment: BTPEnvironment) -> dict[str, object]:
    return {
        "scope_type": "environment",
        "btp_account_id": environment.btp_account_id,
        "btp_environment_id": environment.id,
    }


async def _authorized_environment(
    session: AsyncSession,
    principal: Principal,
    environment_id: uuid.UUID,
    permission: Permission,
) -> BTPEnvironment:
    coverage = await coverage_for(session, principal, permission)
    environment = await session.get(BTPEnvironment, environment_id)
    if environment is None or not coverage.covers_environment(environment_id):
        raise NotFoundError(ENVIRONMENT_NOT_FOUND)
    return environment


async def _authorized_connection(
    session: AsyncSession,
    principal: Principal,
    connection_id: uuid.UUID,
    permission: Permission,
    *,
    lock: bool = False,
) -> tuple[BTPConnection, BTPEnvironment, Coverage]:
    coverage = await coverage_for(session, principal, permission)
    statement = select(BTPConnection, BTPEnvironment).join(
        BTPEnvironment, BTPEnvironment.id == BTPConnection.btp_environment_id
    ).where(BTPConnection.id == connection_id)
    if lock:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).one_or_none()
    if row is None or not coverage.covers_environment(row[1].id):
        raise NotFoundError(CONNECTION_NOT_FOUND)
    return row[0], row[1], coverage


def _status(connection: BTPConnection, runtime_type: str) -> str:
    if not connection.enabled:
        return "disabled"
    if runtime_type == "neo":
        return "unsupported"
    return "enabled"


def _read(
    connection: BTPConnection,
    environment: BTPEnvironment,
    coverage: Coverage,
    resolver: CredentialResolver,
) -> BTPConnectionRead:
    return BTPConnectionRead(
        id=connection.id,
        display_name=connection.display_name,
        btp_account_id=environment.btp_account_id if coverage.covers_account(environment.btp_account_id) else None,
        btp_environment_id=environment.id,
        environment_name=environment.name,
        runtime_type=environment.runtime_type,
        api_endpoint=connection.api_endpoint,
        authentication_method=connection.authentication_method,
        credential_configured=resolver.is_registered(connection.credential_reference),
        enabled=connection.enabled,
        status=_status(connection, environment.runtime_type),
        last_validation_at=connection.last_validation_at,
        last_validation_result=ConnectionValidationResult(connection.last_validation_result),
        last_validation_error_category=connection.last_validation_error_category,
        revision=connection.revision,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


async def list_connections(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    limit: int,
    offset: int,
) -> tuple[list[BTPConnectionRead], bool]:
    coverage = await coverage_for(session, principal, Permission.BTP_CONNECTIONS_READ)
    if not coverage.environment_ids:
        return [], False
    rows = list(
        await session.execute(
            select(BTPConnection, BTPEnvironment)
            .join(BTPEnvironment, BTPEnvironment.id == BTPConnection.btp_environment_id)
            .where(BTPConnection.btp_environment_id.in_(coverage.environment_ids))
            .order_by(BTPConnection.updated_at.desc(), BTPConnection.id)
            .limit(limit + 1)
            .offset(offset)
        )
    )
    resolver = CredentialResolver(settings)
    return [
        _read(connection, environment, coverage, resolver) for connection, environment in rows[:limit]
    ], len(rows) > limit


async def get_connection(
    session: AsyncSession, principal: Principal, settings: Settings, connection_id: uuid.UUID
) -> BTPConnectionRead:
    connection, environment, coverage = await _authorized_connection(
        session, principal, connection_id, Permission.BTP_CONNECTIONS_READ
    )
    return _read(connection, environment, coverage, CredentialResolver(settings))


async def available_environments(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    *,
    limit: int,
    offset: int,
) -> tuple[list[AvailableEnvironmentRead], bool]:
    coverage = await coverage_for(session, principal, Permission.BTP_ENVIRONMENT_SELECT)
    if not coverage.environment_ids:
        return [], False
    rows = list(
        await session.execute(
            select(BTPEnvironment, BTPConnection)
            .outerjoin(BTPConnection, BTPConnection.btp_environment_id == BTPEnvironment.id)
            .where(BTPEnvironment.id.in_(coverage.environment_ids))
            .order_by(BTPEnvironment.name, BTPEnvironment.id)
            .limit(limit + 1)
            .offset(offset)
        )
    )
    items = []
    for environment, connection in rows[:limit]:
        if connection is None:
            state = "not_configured"
        elif not connection.enabled:
            state = "disabled"
        elif environment.runtime_type == "neo":
            state = "unsupported"
        else:
            state = "available"
        items.append(
            AvailableEnvironmentRead(
                id=environment.id,
                name=environment.name,
                btp_account_id=environment.btp_account_id if coverage.covers_account(environment.btp_account_id) else None,
                runtime_type=environment.runtime_type,
                connection_id=connection.id if connection else None,
                connection_name=connection.display_name if connection else None,
                enabled=connection.enabled if connection else False,
                status=state,
                validation_result=(
                    ConnectionValidationResult(connection.last_validation_result)
                    if connection else None
                ),
            )
        )
    return items, len(rows) > limit


def _configuration_error(outcome: ValidationOutcome) -> BadRequestError:
    return BadRequestError(
        "The BTP connection configuration is not valid.",
        code=outcome.error_category or outcome.result.value,
    )


async def create_connection(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    data: BTPConnectionCreate,
) -> BTPConnectionRead:
    environment = await _authorized_environment(
        session, principal, data.btp_environment_id, Permission.BTP_CONNECTIONS_CREATE
    )
    resolver = CredentialResolver(settings)
    connection = BTPConnection(
        display_name=data.display_name,
        btp_account_id=environment.btp_account_id,
        btp_environment_id=environment.id,
        api_endpoint=data.api_endpoint.rstrip("/"),
        credential_reference=data.credential_reference,
        authentication_method=data.authentication_method,
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    outcome = validate_configuration(connection, environment.runtime_type, resolver, settings)
    if outcome.result is not ConnectionValidationResult.VALID_CONFIGURATION:
        raise _configuration_error(outcome)
    session.add(connection)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(
            "A connection already exists for this BTP environment.", code="connection_exists"
        ) from None
    audit_service.record(
        session,
        AuditEventType.BTP_CONNECTION_REGISTERED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"connection_id": str(connection.id), "display_name": connection.display_name, "authentication_method": connection.authentication_method},
        **_scope(environment),
    )
    await session.commit()
    return _read(connection, environment, await coverage_for(session, principal, Permission.BTP_CONNECTIONS_CREATE), resolver)


async def update_connection(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    connection_id: uuid.UUID,
    data: BTPConnectionUpdate,
) -> BTPConnectionRead:
    connection, environment, coverage = await _authorized_connection(
        session, principal, connection_id, Permission.BTP_CONNECTIONS_UPDATE, lock=True
    )
    if connection.revision != data.revision:
        await session.rollback()
        raise ConflictError("This BTP connection changed. Reload it and try again.", code="revision_conflict")
    changes = data.model_dump(exclude_unset=True, exclude={"revision"})
    for key, value in changes.items():
        setattr(connection, key, value.rstrip("/") if key == "api_endpoint" else value)
    resolver = CredentialResolver(settings)
    outcome = validate_configuration(connection, environment.runtime_type, resolver, settings)
    if outcome.result is not ConnectionValidationResult.VALID_CONFIGURATION:
        await session.rollback()
        raise _configuration_error(outcome)
    connection.updated_by = principal.user_id
    connection.revision += 1
    if set(changes) & {"api_endpoint", "credential_reference", "authentication_method"}:
        connection.last_validation_at = None
        connection.last_validation_result = ConnectionValidationResult.NOT_VALIDATED.value
        connection.last_validation_error_category = None
    audit_service.record(
        session,
        AuditEventType.BTP_CONNECTION_UPDATED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"connection_id": str(connection.id), "fields": sorted(changes)},
        **_scope(environment),
    )
    await session.commit()
    return _read(connection, environment, coverage, resolver)


async def disable_connection(
    session: AsyncSession, principal: Principal, settings: Settings, connection_id: uuid.UUID
) -> BTPConnectionRead:
    connection, environment, coverage = await _authorized_connection(
        session, principal, connection_id, Permission.BTP_CONNECTIONS_UPDATE, lock=True
    )
    connection.enabled = False
    connection.updated_by = principal.user_id
    connection.revision += 1
    audit_service.record(
        session,
        AuditEventType.BTP_CONNECTION_DISABLED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"connection_id": str(connection.id)},
        **_scope(environment),
    )
    await session.commit()
    return _read(connection, environment, coverage, CredentialResolver(settings))


async def validate_connection(
    session: AsyncSession,
    principal: Principal,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    connection_id: uuid.UUID,
    *,
    live: bool,
) -> tuple[BTPConnectionRead, ValidationOutcome]:
    connection, environment, coverage = await _authorized_connection(
        session, principal, connection_id, Permission.BTP_CONNECTIONS_VALIDATE
    )
    audit_details = {"connection_id": str(connection.id), "live_requested": live}
    await audit_service.record_standalone(
        session_factory,
        AuditEventType.BTP_CONNECTION_VALIDATION_REQUESTED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details=audit_details,
        **_scope(environment),
    )
    resolver = CredentialResolver(settings)
    if not connection.enabled:
        outcome = ValidationOutcome(ConnectionValidationResult.CONNECTION_DISABLED, "connection_disabled")
    elif live:
        if not settings.BTP_LIVE_VALIDATION_ENABLED:
            raise ServiceUnavailableError(
                "Live SAP BTP validation is disabled by server policy.",
                code="btp_live_validation_not_approved",
            )
        outcome = await validate_live(connection, environment.runtime_type, resolver, settings)
    else:
        outcome = validate_configuration(connection, environment.runtime_type, resolver, settings)
    async with session_factory() as write_session:
        stored = await write_session.get(BTPConnection, connection.id, with_for_update=True)
        if stored is not None:
            stored.last_validation_at = datetime.now(UTC)
            stored.last_validation_result = outcome.result.value
            stored.last_validation_error_category = outcome.error_category
            await write_session.commit()
    event = (
        AuditEventType.BTP_CONNECTION_VALIDATION_SUCCEEDED
        if outcome.result in {ConnectionValidationResult.VALID_CONFIGURATION, ConnectionValidationResult.VALIDATED}
        else AuditEventType.BTP_CONNECTION_VALIDATION_FAILED
    )
    await audit_service.record_standalone(
        session_factory,
        event,
        Outcome.SUCCESS if event is AuditEventType.BTP_CONNECTION_VALIDATION_SUCCEEDED else Outcome.FAILURE,
        actor_user_id=principal.user_id,
        details={"connection_id": str(connection.id), "result": outcome.result.value, "error_category": outcome.error_category},
        **_scope(environment),
    )
    async with session_factory() as read_session:
        refreshed = await read_session.get(BTPConnection, connection.id)
        assert refreshed is not None
        return _read(refreshed, environment, coverage, resolver), outcome


async def select_active_connection(
    session: AsyncSession,
    principal: Principal,
    connection_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> BTPConnectionRead:
    connection, environment, coverage = await _authorized_connection(
        session, principal, connection_id, Permission.BTP_ENVIRONMENT_SELECT
    )
    if not connection.enabled:
        raise BadRequestError("Disabled connections cannot be selected.", code="connection_disabled")
    selection = await session.get(BTPActiveConnection, principal.user_id, with_for_update=True)
    if selection is None:
        selection = BTPActiveConnection(user_id=principal.user_id, connection_id=connection.id)
        session.add(selection)
    else:
        selection.connection_id = connection.id
    audit_service.record(
        session,
        AuditEventType.BTP_ENVIRONMENT_SELECTED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        details={"connection_id": str(connection.id)},
        **_scope(environment),
    )
    await session.commit()
    return _read(connection, environment, coverage, CredentialResolver(settings))


async def active_connection(
    session: AsyncSession, principal: Principal, settings: Settings
) -> BTPConnectionRead | None:
    selection = await session.get(BTPActiveConnection, principal.user_id)
    if selection is None:
        return None
    try:
        connection, environment, coverage = await _authorized_connection(
            session, principal, selection.connection_id, Permission.BTP_ENVIRONMENT_SELECT
        )
    except NotFoundError:
        return None
    if not connection.enabled:
        return None
    return _read(connection, environment, coverage, CredentialResolver(settings))


async def authorized_connection_context(
    session: AsyncSession, principal: Principal, settings: Settings
) -> BTPConnectionContext | None:
    connection_read = await active_connection(session, principal, settings)
    if connection_read is None:
        return None
    connection, environment, _ = await _authorized_connection(
        session, principal, connection_read.id, Permission.BTP_ENVIRONMENT_SELECT
    )
    return BTPConnectionContext(
        connection_id=connection.id,
        runtime_type=environment.runtime_type,
        btp_account_id=environment.btp_account_id,
        btp_environment_id=environment.id,
        api_endpoint=connection.api_endpoint,
        credential_reference=connection.credential_reference,
        authentication_method=connection.authentication_method,
        credential_resolver=CredentialResolver(settings),
        timeout_seconds=settings.BTP_CONNECTION_TIMEOUT_SECONDS,
        max_retries=settings.BTP_CONNECTION_MAX_RETRIES,
    )
