from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ReplyStatus(enum.StrEnum):
    GENERATING = "generating"
    FAILED = "failed"
    COMPLETED = "completed"


_ROLE_VALUES = ", ".join(f"'{role.value}'" for role in MessageRole)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Idempotency key: a retried submission maps onto the row it already created.
        # NULL client ids (server-generated messages) never collide.
        UniqueConstraint("conversation_id", "client_message_id"),
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="role_valid"),
        CheckConstraint("char_length(content) > 0", name="content_not_empty"),
        Index("ix_messages_conversation_id_seq", "conversation_id", "seq"),
        # Agent replies: at most one per user message, in the same conversation, assistant
        # role only. The unique key also stops a retried request from paying for a second
        # AI call once the first reply is stored.
        UniqueConstraint("conversation_id", "id"),
        UniqueConstraint("reply_to_message_id"),
        ForeignKeyConstraint(
            ["conversation_id", "reply_to_message_id"],
            ["messages.conversation_id", "messages.id"],
            name="fk_messages_reply_to_message_id_messages",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "reply_to_message_id IS NULL OR role = 'assistant'", name="reply_from_assistant"
        ),
        CheckConstraint(
            "(reply_status IS NULL AND reply_status_at IS NULL) OR "
            "(role = 'user' AND reply_status IN ('generating', 'failed', 'completed') "
            "AND reply_status_at IS NOT NULL)",
            name="reply_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Monotonic insertion order; gives deterministic ordering and pagination cursors.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Assistant replies only: route, outcome, sources and tool statuses. Never secrets.
    agent_metadata: Mapped[dict | None] = mapped_column(JSONB)
    # User messages only: the latest agent-reply attempt (ReplyStatus) and when it changed.
    # NULL means a reply was never requested.
    reply_status: Mapped[str | None] = mapped_column(String(16))
    reply_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")
