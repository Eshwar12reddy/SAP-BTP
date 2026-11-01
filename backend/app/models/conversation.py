from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.message import Message

TITLE_MAX_LENGTH = 200
DEFAULT_TITLE = "New conversation"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
        # Serves the "most recently active first" listing (scanned backwards).
        Index("ix_conversations_updated_at_id", "updated_at", "id"),
    )
    # Load server-generated timestamps via RETURNING on insert.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(
        String(TITLE_MAX_LENGTH), nullable=False, server_default=DEFAULT_TITLE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped whenever a message is added, so it doubles as the activity timestamp.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", passive_deletes=True, lazy="raise"
    )
