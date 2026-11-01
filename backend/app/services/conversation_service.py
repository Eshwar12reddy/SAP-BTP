"""Conversation and message persistence. Every write commits before returning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Conversation, Message, MessageRole
from app.models.conversation import DEFAULT_TITLE

AUTO_TITLE_MAX_LENGTH = 80
_FOREIGN_KEY_VIOLATION = "23503"


@dataclass(frozen=True)
class SavedMessage:
    message: Message
    created: bool  # False when an identical retry was replayed


async def create_conversation(session: AsyncSession, title: str | None) -> Conversation:
    conversation = Conversation(title=title or DEFAULT_TITLE)
    session.add(conversation)
    await session.commit()
    return conversation


async def list_conversations(
    session: AsyncSession, *, limit: int, offset: int
) -> tuple[list[Conversation], bool]:
    rows = await session.scalars(
        select(Conversation)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit + 1)
        .offset(offset)
    )
    conversations = list(rows)
    return conversations[:limit], len(conversations) > limit


async def get_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


async def list_messages(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    limit: int,
    before: uuid.UUID | None = None,
) -> tuple[list[Message], bool]:
    """Return the newest ``limit`` messages (older than ``before``) in chronological order."""
    await get_conversation(session, conversation_id)

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
    conversation_id: uuid.UUID,
    *,
    content: str,
    client_message_id: uuid.UUID,
) -> SavedMessage:
    """Insert a user message idempotently.

    The (conversation_id, client_message_id) unique constraint arbitrates concurrent
    retries: ``ON CONFLICT DO NOTHING`` waits for any in-flight insert of the same key,
    so exactly one request creates the row and the others replay or conflict.
    """
    await get_conversation(session, conversation_id)

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
            raise NotFoundError("Conversation not found.") from None
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


def _derive_title(content: str) -> str:
    first_line = next((line for line in content.splitlines() if line.strip()), content)
    title = " ".join(first_line.split())
    if len(title) > AUTO_TITLE_MAX_LENGTH:
        title = title[: AUTO_TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return title
