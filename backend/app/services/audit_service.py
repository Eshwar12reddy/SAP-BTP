"""Security audit events.

``record`` adds the event to the caller's transaction, so an administrative change and
its audit row commit (or roll back) together. ``record_standalone`` is for outcomes whose
own transaction does not commit (failed logins, access denials).
"""

from __future__ import annotations

import enum
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import client_ip_var, request_id_var
from app.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditEventType(enum.StrEnum):
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILURE = "auth.login.failure"
    LOGIN_RATE_LIMITED = "auth.login.rate_limited"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    REFRESH_REUSE_DETECTED = "auth.refresh.reuse_detected"
    PASSWORD_CHANGED = "auth.password.changed"
    PASSWORD_CHANGE_FAILED = "auth.password.change_failed"
    USER_CREATED = "admin.user.created"
    USER_UPDATED = "admin.user.updated"
    USER_DISABLED = "admin.user.disabled"
    USER_ENABLED = "admin.user.enabled"
    GRANT_ASSIGNED = "admin.grant.assigned"
    GRANT_REVOKED = "admin.grant.revoked"
    SESSIONS_REVOKED = "admin.sessions.revoked"
    ADMIN_BOOTSTRAPPED = "admin.bootstrap"
    LEGACY_CONVERSATIONS_ASSIGNED = "admin.legacy_conversations.assigned"
    BTP_MAPPING_CHANGED = "admin.btp_mapping.changed"
    BTP_CONNECTION_REGISTERED = "btp.connection.registered"
    BTP_CONNECTION_UPDATED = "btp.connection.updated"
    BTP_CONNECTION_DISABLED = "btp.connection.disabled"
    BTP_CONNECTION_VALIDATION_REQUESTED = "btp.connection.validation.requested"
    BTP_CONNECTION_VALIDATION_SUCCEEDED = "btp.connection.validation.succeeded"
    BTP_CONNECTION_VALIDATION_FAILED = "btp.connection.validation.failed"
    BTP_ENVIRONMENT_SELECTED = "btp.environment.selected"
    BTP_CONNECTION_ACCESS_DENIED = "btp.connection.access_denied"
    BTP_DIAGNOSIS_REQUESTED = "btp.diagnosis.requested"
    BTP_DIAGNOSIS_COMPLETED = "btp.diagnosis.completed"
    BTP_HEALTH_REQUESTED = "btp.health.requested"
    BTP_HEALTH_COMPLETED = "btp.health.completed"
    BTP_RECOMMENDATION_REQUESTED = "btp.recommendation.requested"
    BTP_RECOMMENDATION_COMPLETED = "btp.recommendation.completed"
    BTP_SIZING_COMPLETED = "btp.sizing.completed"
    BTP_ARCHITECTURE_REQUESTED = "btp.architecture.requested"
    BTP_ARCHITECTURE_COMPLETED = "btp.architecture.completed"
    BTP_INFRASTRUCTURE_PLAN_GENERATED = "btp.infrastructure_plan.generated"
    BTP_INFRASTRUCTURE_PLAN_VALIDATED = "btp.infrastructure_plan.validated"
    ACCESS_DENIED = "authz.access_denied"
    KNOWLEDGE_SOURCE_CREATED = "knowledge.source.created"
    KNOWLEDGE_SOURCE_UPDATED = "knowledge.source.updated"
    KNOWLEDGE_SOURCE_REMOVED = "knowledge.source.removed"
    KNOWLEDGE_VERSION_UPLOADED = "knowledge.version.uploaded"
    KNOWLEDGE_VERSION_VALIDATED = "knowledge.version.validated"
    KNOWLEDGE_VERSION_REJECTED = "knowledge.version.rejected"
    KNOWLEDGE_INDEX_FAILED = "knowledge.index.failed"
    KNOWLEDGE_ACCESS_DENIED = "knowledge.access.denied"
    MEMORY_CREATED = "agent.memory.created"
    MEMORY_UPDATED = "agent.memory.updated"
    MEMORY_DELETED = "agent.memory.deleted"
    MEMORY_ACCESS_DENIED = "agent.memory.access_denied"
    INVESTIGATION_STARTED = "agent.investigation.started"
    INVESTIGATION_COMPLETED = "agent.investigation.completed"
    INVESTIGATION_DENIED = "agent.investigation.denied"
    APPROVAL_REQUESTED = "agent.approval.requested"
    APPROVAL_EXPLAINED = "agent.approval.explained"
    APPROVAL_ALTERNATIVE_REQUESTED = "agent.approval.alternative_requested"
    APPROVAL_ACCEPTED = "agent.approval.accepted"
    APPROVAL_CANCELLED = "agent.approval.cancelled"
    APPROVAL_EXPIRED = "agent.approval.expired"


class Outcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


def build_event(
    event_type: AuditEventType,
    outcome: Outcome,
    *,
    actor_user_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    btp_account_id: uuid.UUID | None = None,
    btp_environment_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type.value,
        outcome=outcome.value,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        scope_type=scope_type,
        btp_account_id=btp_account_id,
        btp_environment_id=btp_environment_id,
        request_id=request_id_var.get(),
        ip_address=client_ip_var.get(),
        details=details or {},
    )


def record(session: AsyncSession, event_type: AuditEventType, outcome: Outcome, **fields) -> None:
    """Add to the caller's transaction. Rows the event references (user, account) must
    already be flushed: the unit of work does not order inserts across unrelated mappers."""
    session.add(build_event(event_type, outcome, **fields))


async def record_standalone(
    session_factory: async_sessionmaker[AsyncSession],
    event_type: AuditEventType,
    outcome: Outcome,
    **fields,
) -> None:
    """Commit an audit event in its own transaction. Failures are logged, never raised:
    an audit write must not turn a 401/403 into a 500."""
    try:
        async with session_factory() as session:
            session.add(build_event(event_type, outcome, **fields))
            await session.commit()
    except Exception:
        logger.exception(
            "Failed to write audit event",
            extra={"event": "audit_write_failed", "error_code": event_type.value},
        )
