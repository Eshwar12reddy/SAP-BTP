"""Conversation and message persistence. Every write commits before returning.

Every query is restricted to the authenticated owner. Legacy conversations (owner NULL)
never match, so they stay invisible until ownership is assigned explicitly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.auth.authorization import Coverage
from app.core.exceptions import ConflictError, NotFoundError
from app.models import Conversation, Message, MessageRole
from app.models.conversation import DEFAULT_TITLE
from app.models.message import ReplyStatus

AUTO_TITLE_MAX_LENGTH = 80
_FOREIGN_KEY_VIOLATION = "23503"


@dataclass(frozen=True)
class SavedMessage:
    message: Message
    created: bool  # False when an identical retry was replayed


NOT_FOUND = "Conversation not found."


async def create_conversation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    title: str | None,
    btp_account_id: uuid.UUID | None = None,
    btp_environment_id: uuid.UUID | None = None,
) -> Conversation:
    conversation = Conversation(
        owner_id=owner_id,
        title=title or DEFAULT_TITLE,
        btp_account_id=btp_account_id,
        btp_environment_id=btp_environment_id,
    )
    session.add(conversation)
    await session.commit()
    return conversation


def context_authorized(conversation: Conversation, coverage: Coverage) -> bool:
    """A conversation's BTP context (if any) must still be readable by the owner."""
    if conversation.btp_account_id is not None:
        return coverage.covers_account(conversation.btp_account_id)
    if conversation.btp_environment_id is not None:
        return coverage.covers_environment(conversation.btp_environment_id)
    return True


async def list_conversations(
    session: AsyncSession, *, owner_id: uuid.UUID, coverage: Coverage, limit: int, offset: int
) -> tuple[list[Conversation], bool]:
    # Context filtering happens before pagination: revoked contexts are simply absent.
    account_ok = Conversation.btp_account_id.is_(None)
    if coverage.account_ids:
        account_ok = or_(account_ok, Conversation.btp_account_id.in_(coverage.account_ids))
    environment_ok = Conversation.btp_environment_id.is_(None)
    if coverage.environment_ids:
        environment_ok = or_(
            environment_ok, Conversation.btp_environment_id.in_(coverage.environment_ids)
        )
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.owner_id == owner_id, account_ok, environment_ok)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    conversations = list(rows)
    return conversations[:limit], len(conversations) > limit


async def get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, *, owner_id: uuid.UUID
) -> Conversation:
    """Another user's conversation is indistinguishable from a missing one."""
    conversation = await session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner_id
        )
    )
    if conversation is None:
        raise NotFoundError(NOT_FOUND)
    return conversation


async def list_messages(
    session: AsyncSession,
    conversation: Conversation,
    *,
    limit: int,
    before: uuid.UUID | None = None,
) -> tuple[list[Message], bool]:
    """Return the newest ``limit`` messages (older than ``before``) in chronological order."""
    conversation_id = conversation.id
    query = select(Message).where(Message.conversation_id == conversation_id)
    if before is not None:
        cursor_seq = await session.scalar(
            select(Message.seq).where(
                Message.id == before, Message.conversation_id == conversation_id
            )
        )
        if cursor_seq is None:
            raise NotFoundError("Message cursor not found in this conversation.")
        query = query.where(Message.seq < cursor_seq)

    rows = list(await session.scalars(query.order_by(Message.seq.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    return list(reversed(rows[:limit])), has_more


async def add_user_message(
    session: AsyncSession,
    conversation: Conversation,
    *,
    content: str,
    client_message_id: uuid.UUID,
) -> SavedMessage:
    """Insert a user message idempotently.

    The (conversation_id, client_message_id) unique constraint arbitrates concurrent
    retries: ``ON CONFLICT DO NOTHING`` waits for any in-flight insert of the same key,
    so exactly one request creates the row and the others replay or conflict.
    """
    conversation_id = conversation.id

    statement = (
        insert(Message)
        .values(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            role=MessageRole.USER.value,
            content=content,
            client_message_id=client_message_id,
        )
        .on_conflict_do_nothing(index_elements=["conversation_id", "client_message_id"])
        .returning(Message)
    )
    try:
        message = (await session.scalars(statement)).one_or_none()
        if message is None:
            return await _replay_existing(session, conversation_id, client_message_id, content)

        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                updated_at=func.now(),
                # The first message names a conversation that still has the default title.
                title=case(
                    (Conversation.title == DEFAULT_TITLE, _derive_title(content)),
                    else_=Conversation.title,
                ),
            )
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == _FOREIGN_KEY_VIOLATION:
            raise NotFoundError(NOT_FOUND) from None
        raise
    return SavedMessage(message=message, created=True)


async def _replay_existing(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    client_message_id: uuid.UUID,
    content: str,
) -> SavedMessage:
    existing = await session.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id == client_message_id,
        )
    )
    # Nothing was written; end the read-only transaction (commit keeps `existing` loaded).
    await session.commit()
    if existing is None or existing.role != MessageRole.USER or existing.content != content:
        raise ConflictError(
            "A different message was already submitted with this client_message_id."
        )
    return SavedMessage(message=existing, created=False)


async def get_user_message(
    session: AsyncSession, conversation: Conversation, message_id: uuid.UUID
) -> Message:
    message = await session.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation.id,
            Message.role == MessageRole.USER.value,
        )
    )
    if message is None:
        raise NotFoundError("Message not found.")
    return message


async def find_reply(session: AsyncSession, message: Message) -> Message | None:
    return await session.scalar(select(Message).where(Message.reply_to_message_id == message.id))


async def claim_reply(session: AsyncSession, message: Message, *, stale_before: datetime) -> bool:
    """Mark ``message`` as having a reply in progress. Commits.

    One conditional UPDATE, so exactly one request wins even across worker processes.
    Claimable: never requested, failed, or a "generating" claim older than
    ``stale_before`` (its worker died or gave up). A completed reply is never claimed.
    """
    claimed = await session.scalar(
        update(Message)
        .where(
            Message.id == message.id,
            or_(
                Message.reply_status.is_(None),
                Message.reply_status == ReplyStatus.FAILED.value,
                and_(
                    Message.reply_status == ReplyStatus.GENERATING.value,
                    Message.reply_status_at < stale_before,
                ),
            ),
        )
        .values(reply_status=ReplyStatus.GENERATING.value, reply_status_at=func.now())
        .returning(Message.id)
    )
    await session.commit()
    return claimed is not None


async def mark_reply_failed(session: AsyncSession, message_id: uuid.UUID) -> None:
    """Record a failed attempt in a fresh transaction (the current one may be broken)."""
    await session.rollback()
    await session.execute(
        update(Message)
        .where(Message.id == message_id, Message.reply_status == ReplyStatus.GENERATING.value)
        .values(reply_status=ReplyStatus.FAILED.value, reply_status_at=func.now())
    )
    await session.commit()


def effective_reply_status(message: Message, *, stale_before: datetime) -> ReplyStatus | None:
    """The reply state a client should see; an abandoned "generating" claim is a failure."""
    if message.reply_status is None:
        return None
    status = ReplyStatus(message.reply_status)
    if status is ReplyStatus.GENERATING and message.reply_status_at < stale_before:  # type: ignore[operator]
        return ReplyStatus.FAILED
    return status


async def add_assistant_reply(
    session: AsyncSession,
    message: Message,
    *,
    content: str,
    agent_metadata: dict,
) -> SavedMessage:
    """Store the reply to ``message``. If another request stored one first, return that one.

    The unique key on ``reply_to_message_id`` arbitrates, so a user message never gets two
    stored replies, even across worker processes.
    """
    statement = (
        insert(Message)
        .values(
            id=uuid.uuid4(),
            conversation_id=message.conversation_id,
            role=MessageRole.ASSISTANT.value,
            content=content,
            reply_to_message_id=message.id,
            agent_metadata=agent_metadata,
        )
        .on_conflict_do_nothing(index_elements=["reply_to_message_id"])
        .returning(Message)
    )
    reply = (await session.scalars(statement)).one_or_none()
    # Completed either way; set in the same transaction as the reply itself.
    await session.execute(
        update(Message)
        .where(Message.id == message.id)
        .values(reply_status=ReplyStatus.COMPLETED.value, reply_status_at=func.now())
    )
    if reply is None:
        existing = await find_reply(session, message)
        await session.commit()
        if existing is None:
            raise NotFoundError("Message not found.")
        return SavedMessage(message=existing, created=False)
    await session.execute(
        update(Conversation)
        .where(Conversation.id == message.conversation_id)
        .values(updated_at=func.now())
    )
    await session.commit()
    return SavedMessage(message=reply, created=True)


def _derive_title(content: str) -> str:
    first_line = next((line for line in content.splitlines() if line.strip()), content)
    title = " ".join(first_line.split())
    if len(title) > AUTO_TITLE_MAX_LENGTH:
        title = title[: AUTO_TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return title
