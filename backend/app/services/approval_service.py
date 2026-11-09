"""Durable, owner-bound human approval records for proposal-only agent runs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentApproval, ApprovalDecision, ApprovalStatus
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

APPROVAL_TTL = timedelta(minutes=15)


def fingerprint_for(*, user_id: uuid.UUID, thread_id: str, conversation_id: uuid.UUID, operation_type: str, canonical_args: dict) -> str:
    payload = {
        "user_id": str(user_id),
        "thread_id": thread_id,
        "conversation_id": str(conversation_id),
        "operation_type": operation_type,
        "canonical_args": canonical_args,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def get_for_thread(
    session: AsyncSession, *, user_id: uuid.UUID, thread_id: str, conversation_id: uuid.UUID
) -> AgentApproval | None:
    return await session.scalar(
        select(AgentApproval)
        .where(
            AgentApproval.user_id == user_id,
            AgentApproval.thread_id == thread_id,
            AgentApproval.conversation_id == conversation_id,
        )
        .order_by(AgentApproval.created_at.desc())
        .limit(1)
    )


async def get_owned(
    session: AsyncSession, approval_id: uuid.UUID, *, user_id: uuid.UUID, thread_id: str, conversation_id: uuid.UUID
) -> AgentApproval | None:
    return await session.scalar(
        select(AgentApproval).where(
            AgentApproval.id == approval_id,
            AgentApproval.user_id == user_id,
            AgentApproval.thread_id == thread_id,
            AgentApproval.conversation_id == conversation_id,
        )
    )


async def create_or_get_pending(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    thread_id: str,
    conversation_id: uuid.UUID,
    operation_type: str,
    operation_summary: str,
    canonical_args: dict,
    risk_level: str = "high",
) -> AgentApproval:
    fingerprint = fingerprint_for(
        user_id=user_id,
        thread_id=thread_id,
        conversation_id=conversation_id,
        operation_type=operation_type,
        canonical_args=canonical_args,
    )
    existing = await session.scalar(
        select(AgentApproval).where(AgentApproval.fingerprint == fingerprint).with_for_update()
    )
    now = datetime.now(UTC)
    if existing is not None:
        if existing.status == ApprovalStatus.PENDING.value and existing.expires_at <= now:
            existing.status = ApprovalStatus.EXPIRED.value
            existing.result_summary = "The approval expired before a decision was submitted."
            existing.decision_at = now
            audit_service.record(
                session,
                AuditEventType.APPROVAL_EXPIRED,
                Outcome.FAILURE,
                actor_user_id=user_id,
                details={"approval_id": str(existing.id)},
            )
            await session.commit()
        return existing
    approval = AgentApproval(
        id=uuid.uuid4(),
        user_id=user_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        operation_type=operation_type,
        operation_summary=operation_summary[:4000],
        canonical_args=canonical_args,
        fingerprint=fingerprint,
        risk_level=risk_level,
        status=ApprovalStatus.PENDING.value,
        execution_available=False,
        expires_at=now + APPROVAL_TTL,
    )
    session.add(approval)
    audit_service.record(
        session,
        AuditEventType.APPROVAL_REQUESTED,
        Outcome.SUCCESS,
        actor_user_id=user_id,
        target_user_id=user_id,
        details={"approval_id": str(approval.id), "operation_type": operation_type, "risk_level": risk_level},
    )
    try:
        await session.commit()
    except IntegrityError:
        # Another worker may have created the same deterministic proposal between
        # the lookup and commit. Never create a second approval for one fingerprint.
        await session.rollback()
        existing = await session.scalar(select(AgentApproval).where(AgentApproval.fingerprint == fingerprint))
        if existing is None:
            raise
        return existing
    return approval


async def record_nonterminal_choice(
    session: AsyncSession,
    approval: AgentApproval,
    *,
    actor_user_id: uuid.UUID,
    decision: ApprovalDecision,
) -> AgentApproval:
    if decision not in (ApprovalDecision.EXPLAIN_COMMAND, ApprovalDecision.OTHER_APPROACH):
        raise ValueError("Only explanatory or alternative choices are non-terminal.")
    if approval.status == ApprovalStatus.PENDING.value and approval.expires_at <= datetime.now(UTC):
        approval.status = ApprovalStatus.EXPIRED.value
        approval.decision_at = datetime.now(UTC)
        approval.result_summary = "The approval expired before a decision was submitted."
        audit_service.record(session, AuditEventType.APPROVAL_EXPIRED, Outcome.FAILURE, actor_user_id=actor_user_id, details={"approval_id": str(approval.id)})
    elif approval.status == ApprovalStatus.PENDING.value:
        event = AuditEventType.APPROVAL_EXPLAINED if decision is ApprovalDecision.EXPLAIN_COMMAND else AuditEventType.APPROVAL_ALTERNATIVE_REQUESTED
        audit_service.record(session, event, Outcome.SUCCESS, actor_user_id=actor_user_id, details={"approval_id": str(approval.id)})
    await session.commit()
    return approval


async def apply_decision(
    session: AsyncSession,
    approval: AgentApproval,
    *,
    actor_user_id: uuid.UUID,
    decision: ApprovalDecision,
) -> AgentApproval:
    """Atomically apply Yes/No after the LangGraph interrupt is resumed."""
    if decision not in (ApprovalDecision.EXECUTE, ApprovalDecision.CANCEL):
        raise ValueError("This method only applies terminal decisions.")
    approval = await session.scalar(select(AgentApproval).where(AgentApproval.id == approval.id).with_for_update()) or approval
    now = datetime.now(UTC)
    if approval.status != ApprovalStatus.PENDING.value:
        return approval
    if approval.expires_at <= now:
        approval.status = ApprovalStatus.EXPIRED.value
        approval.decision = decision.value
        approval.decision_by = actor_user_id
        approval.decision_at = now
        approval.result_summary = "The approval expired before a decision was submitted."
        audit_service.record(session, AuditEventType.APPROVAL_EXPIRED, Outcome.FAILURE, actor_user_id=actor_user_id, details={"approval_id": str(approval.id)})
    elif decision is ApprovalDecision.CANCEL:
        approval.status = ApprovalStatus.CANCELLED.value
        approval.decision = decision.value
        approval.decision_by = actor_user_id
        approval.decision_at = now
        approval.result_summary = "The proposed operation was cancelled. No infrastructure change was made."
        audit_service.record(session, AuditEventType.APPROVAL_CANCELLED, Outcome.SUCCESS, actor_user_id=actor_user_id, details={"approval_id": str(approval.id)})
    else:
        # There is intentionally no executor in this phase. Approval is recorded, but
        # the system never claims that an SAP or shell operation occurred.
        approval.status = ApprovalStatus.EXECUTION_UNAVAILABLE.value
        approval.decision = decision.value
        approval.decision_by = actor_user_id
        approval.decision_at = now
        approval.result_summary = "Approved, but no authorized executor is available in this read-only phase. No infrastructure change was made."
        audit_service.record(session, AuditEventType.APPROVAL_ACCEPTED, Outcome.SUCCESS, actor_user_id=actor_user_id, details={"approval_id": str(approval.id), "execution_available": False})
    await session.commit()
    return approval
