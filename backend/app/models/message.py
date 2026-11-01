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
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="raise")
