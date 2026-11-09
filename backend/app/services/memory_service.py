"""Authorized persistence and retrieval for long-term agent memory.

This service is intentionally explicit: chat replies do not automatically become memories.
Only user-confirmed preferences or records tied to an owned, currently authorized
conversation and evidence references can be saved.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import Principal, coverage_for, load_active_grants
from app.auth.permissions import Permission
from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError, PermissionDeniedError
from app.models import Conversation, Memory, MemoryCategory, MemoryVerification, Message
from app.safety.input_guard import sanitize
from app.safety.redaction import Redactor
from app.services import audit_service
from app.services.audit_service import AuditEventType, Outcome

MAX_MEMORY_CONTENT = 8_000
MAX_EVIDENCE_REFERENCES = 20
MAX_EVIDENCE_REFERENCE_LENGTH = 160
def _active_clause(now: datetime):
    return (
        Memory.deleted_at.is_(None),
        Memory.expires_at.is_(None) | (Memory.expires_at > now),
        Memory.verification_status.in_(
            [MemoryVerification.CONFIRMED.value, MemoryVerification.USER_CONFIRMED.value]
        ),
    )


async def _refresh_authorization(session: AsyncSession, principal: Principal):
    principal.grants = await load_active_grants(session, principal.user_id)
    principal._coverage.clear()
    return await coverage_for(session, principal, Permission.BTP_READ)


def _scope_allowed(memory: Memory, coverage) -> bool:
    if memory.btp_account_id is not None:
        return coverage.covers_account(memory.btp_account_id)
    if memory.btp_environment_id is not None:
        return coverage.covers_environment(memory.btp_environment_id)
    return True


async def _owned_memory(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID, *, include_expired: bool = False
) -> tuple[Memory, object]:
    memory = await session.scalar(
        select(Memory).where(Memory.id == memory_id, Memory.owner_user_id == principal.user_id)
    )
    if memory is None or memory.deleted_at is not None:
        raise NotFoundError("Memory not found.")
    coverage = await _refresh_authorization(session, principal)
    if not _scope_allowed(memory, coverage):
        raise NotFoundError("Memory not found.")
    now = datetime.now(UTC)
    if not include_expired and memory.expires_at is not None and memory.expires_at <= now:
        raise NotFoundError("Memory not found.")
    return memory, coverage


async def _source_context(
    session: AsyncSession, principal: Principal, source_conversation_id: uuid.UUID
) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == source_conversation_id,
            Conversation.owner_id == principal.user_id,
        )
    )
    if conversation is None:
        raise NotFoundError("Source conversation not found.")
    coverage = await _refresh_authorization(session, principal)
    if conversation.btp_account_id is not None and not coverage.covers_account(conversation.btp_account_id):
        raise PermissionDeniedError("You no longer have access to the source conversation's BTP scope.")
    if (
        conversation.btp_environment_id is not None
        and not coverage.covers_environment(conversation.btp_environment_id)
    ):
        raise PermissionDeniedError("You no longer have access to the source conversation's BTP scope.")
    return conversation


async def _verified_source_references(
    session: AsyncSession, conversation_id: uuid.UUID
) -> set[str]:
    """Return only evidence labels emitted by a stored backend reply."""

    references: set[str] = set()
    rows = await session.scalars(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.agent_metadata.is_not(None),
        )
    )
    for row in rows:
        metadata = row.agent_metadata if isinstance(row.agent_metadata, dict) else {}
        for source in metadata.get("sources", []):
            references.add(f"source:{source}")
        for result in metadata.get("tool_results", []):
            if isinstance(result, dict):
                references.add(
                    f"tool:{result.get('tool_name', '')}:{result.get('status', '')}"
                )
    return references


async def create_memory(
    session: AsyncSession,
    principal: Principal,
    *,
    category: MemoryCategory,
    content: str,
    btp_account_id: uuid.UUID | None,
    btp_environment_id: uuid.UUID | None,
    source_conversation_id: uuid.UUID | None,
    source_investigation_reference: str | None,
    source_evidence: list[str],
    expires_at: datetime | None,
    redactor: Redactor,
    settings: Settings,
) -> tuple[Memory, bool]:
    if btp_account_id is not None and btp_environment_id is not None:
        raise BadRequestError("A memory may reference an account or environment, not both.")
    safe_evidence = [
        redactor.redact(sanitize(item)[0]).text.strip() for item in source_evidence
    ]
    if len(safe_evidence) > MAX_EVIDENCE_REFERENCES or any(
        len(item) > MAX_EVIDENCE_REFERENCE_LENGTH or not item for item in safe_evidence
    ):
        raise BadRequestError("Memory evidence references are invalid or too numerous.")
    safe_reference = (
        redactor.redact(sanitize(source_investigation_reference)[0]).text.strip()
        if source_investigation_reference
        else None
    )
    clean, _ = sanitize(content)
    redacted = redactor.redact(clean).text.strip()
    if not redacted:
        raise BadRequestError("Memory content must not be blank.")
    if len(redacted) > MAX_MEMORY_CONTENT:
        raise BadRequestError("Memory content is too large.")

    source = None
    if category is MemoryCategory.PREFERENCE:
        if btp_account_id or btp_environment_id or source_conversation_id or safe_evidence:
            raise BadRequestError("Preferences cannot claim a BTP or investigation source.")
        verification = MemoryVerification.USER_CONFIRMED.value
    else:
        if source_conversation_id is None or not safe_evidence:
            raise BadRequestError("Verified memories require an owned conversation and evidence references.")
        source = await _source_context(session, principal, source_conversation_id)
        verified_references = await _verified_source_references(session, source.id)
        if not set(safe_evidence).issubset(verified_references):
            raise BadRequestError("Evidence references must match a stored backend reply.")
        if source.btp_account_id is not None:
            if btp_account_id not in (None, source.btp_account_id) or btp_environment_id is not None:
                raise BadRequestError("Memory scope must match its source conversation.")
            btp_account_id = source.btp_account_id
        elif source.btp_environment_id is not None:
            if btp_environment_id not in (None, source.btp_environment_id) or btp_account_id is not None:
                raise BadRequestError("Memory scope must match its source conversation.")
            btp_environment_id = source.btp_environment_id
        verification = MemoryVerification.CONFIRMED.value

    if btp_account_id is not None or btp_environment_id is not None:
        coverage = await _refresh_authorization(session, principal)
        if btp_account_id is not None and not coverage.covers_account(btp_account_id):
            raise PermissionDeniedError("You do not have access to that BTP account scope.")
        if btp_environment_id is not None and not coverage.covers_environment(btp_environment_id):
            raise PermissionDeniedError("You do not have access to that BTP environment scope.")

    now = datetime.now(UTC)
    expiry = expires_at or now + timedelta(days=settings.AGENT_MEMORY_DEFAULT_TTL_DAYS)
    if expiry <= now or expiry > now + timedelta(days=3650):
        raise BadRequestError("Memory expiry must be in the future and within ten years.")
    content_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    existing = await session.scalar(
        select(Memory).where(
            Memory.owner_user_id == principal.user_id,
            Memory.category == category.value,
            Memory.content_hash == content_hash,
            Memory.deleted_at.is_(None),
            Memory.btp_account_id.is_not_distinct_from(btp_account_id),
            Memory.btp_environment_id.is_not_distinct_from(btp_environment_id),
        )
    )
    if existing is not None:
        existing.last_verified_at = now
        existing.expires_at = expiry
        await session.commit()
        return existing, False

    memory = Memory(
        owner_user_id=principal.user_id,
        btp_account_id=btp_account_id,
        btp_environment_id=btp_environment_id,
        category=category.value,
        content=redacted,
        content_hash=content_hash,
        source_conversation_id=source.id if source else None,
        source_investigation_reference=safe_reference,
        source_evidence=list(safe_evidence),
        verification_status=verification,
        last_verified_at=now,
        expires_at=expiry,
    )
    session.add(memory)
    await session.flush()
    audit_service.record(
        session,
        AuditEventType.MEMORY_CREATED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_account_id=btp_account_id,
        btp_environment_id=btp_environment_id,
        details={"memory_id": str(memory.id), "category": category.value},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(Memory).where(
                Memory.owner_user_id == principal.user_id,
                Memory.category == category.value,
                Memory.content_hash == content_hash,
                Memory.deleted_at.is_(None),
                Memory.btp_account_id.is_not_distinct_from(btp_account_id),
                Memory.btp_environment_id.is_not_distinct_from(btp_environment_id),
            )
        )
        if existing is None:
            raise ConflictError("The memory could not be saved safely.") from None
        return existing, False
    return memory, True


async def list_memories(
    session: AsyncSession, principal: Principal, *, limit: int, offset: int
) -> tuple[list[Memory], bool]:
    coverage = await _refresh_authorization(session, principal)
    now = datetime.now(UTC)
    rows = list(
        await session.scalars(
            select(Memory)
            .where(Memory.owner_user_id == principal.user_id, *_active_clause(now))
            .order_by(Memory.updated_at.desc(), Memory.id.desc())
            .limit(min(limit + offset + 100, 500))
        )
    )
    visible = [memory for memory in rows if _scope_allowed(memory, coverage)]
    page = visible[offset : offset + limit]
    return page, len(visible) > offset + limit


async def get_memory(session: AsyncSession, principal: Principal, memory_id: uuid.UUID) -> Memory:
    memory, _ = await _owned_memory(session, principal, memory_id)
    return memory


async def update_memory(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    *,
    revision: int,
    content: str,
    redactor: Redactor,
) -> Memory:
    memory, _ = await _owned_memory(session, principal, memory_id)
    if memory.revision != revision:
        raise ConflictError("The memory changed; reload it before updating.")
    clean, _ = sanitize(content)
    redacted = redactor.redact(clean).text.strip()
    if not redacted or len(redacted) > MAX_MEMORY_CONTENT:
        raise BadRequestError("Memory content is blank or too large.")
    memory.content = redacted
    memory.content_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    memory.revision += 1
    memory.last_verified_at = datetime.now(UTC)
    audit_service.record(
        session,
        AuditEventType.MEMORY_UPDATED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_account_id=memory.btp_account_id,
        btp_environment_id=memory.btp_environment_id,
        details={"memory_id": str(memory.id), "revision": memory.revision},
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError("An equivalent active memory already exists.") from None
    return memory


async def delete_memory(session: AsyncSession, principal: Principal, memory_id: uuid.UUID) -> None:
    memory, _ = await _owned_memory(session, principal, memory_id)
    memory.deleted_at = datetime.now(UTC)
    audit_service.record(
        session,
        AuditEventType.MEMORY_DELETED,
        Outcome.SUCCESS,
        actor_user_id=principal.user_id,
        btp_account_id=memory.btp_account_id,
        btp_environment_id=memory.btp_environment_id,
        details={"memory_id": str(memory.id)},
    )
    await session.commit()


async def retrieve_memory_context(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    btp_account_id: uuid.UUID | None,
    btp_environment_id: uuid.UUID | None,
    limit: int,
    max_chars: int,
) -> list[Memory]:
    coverage = await _refresh_authorization(session, principal)
    if btp_account_id is not None and not coverage.covers_account(btp_account_id):
        return []
    if btp_environment_id is not None and not coverage.covers_environment(btp_environment_id):
        return []
    now = datetime.now(UTC)
    candidates = list(
        await session.scalars(
            select(Memory)
            .where(Memory.owner_user_id == principal.user_id, *_active_clause(now))
            .order_by(Memory.last_verified_at.desc().nullslast(), Memory.updated_at.desc())
            .limit(min(limit * 8, 160))
        )
    )
    query_terms = {term for term in query.lower().split() if len(term) > 2}
    ranked: list[tuple[int, Memory]] = []
    for memory in candidates:
        if not _scope_allowed(memory, coverage):
            continue
        if btp_account_id is not None and memory.btp_account_id not in (None, btp_account_id):
            continue
        if btp_environment_id is not None and memory.btp_environment_id not in (None, btp_environment_id):
            continue
        text = memory.content.lower()
        score = sum(1 for term in query_terms if term in text)
        if memory.category == MemoryCategory.PREFERENCE.value:
            score += 1
        ranked.append((score, memory))
    ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
    result: list[Memory] = []
    used = 0
    for _, memory in ranked:
        if result and used + len(memory.content) > max_chars:
            break
        result.append(memory)
        used += len(memory.content)
        if len(result) >= limit:
            break
    return result
