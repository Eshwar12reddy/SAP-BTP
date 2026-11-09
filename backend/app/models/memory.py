"""Persistent, bounded agent memories.

Memories are deliberately separate from conversations, LangGraph checkpoints and the
knowledge-document index.  Only explicit user-confirmed preferences or records backed by
an authorized conversation/evidence reference are eligible for retrieval.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MemoryCategory(enum.StrEnum):
    PREFERENCE = "preference"
    VERIFIED_FINDING = "verified_finding"
    RESOURCE_REFERENCE = "resource_reference"
    INCIDENT_PATTERN = "incident_pattern"


class MemoryVerification(enum.StrEnum):
    CONFIRMED = "confirmed"
    USER_CONFIRMED = "user_confirmed"
    HYPOTHESIS = "hypothesis"
    OUTDATED = "outdated"
    REJECTED = "rejected"


RETRIEVABLE_MEMORY_STATUSES = (
    MemoryVerification.CONFIRMED.value,
    MemoryVerification.USER_CONFIRMED.value,
)


class Memory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        CheckConstraint(
            "category IN ('preference', 'verified_finding', 'resource_reference', 'incident_pattern')",
            name="category_valid",
        ),
        CheckConstraint(
            "verification_status IN ('confirmed', 'user_confirmed', 'hypothesis', 'outdated', 'rejected')",
            name="verification_status_valid",
        ),
        CheckConstraint("char_length(btrim(content)) > 0", name="content_not_blank"),
        CheckConstraint("char_length(content) <= 8000", name="content_bounded"),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint(
            "(btp_account_id IS NULL OR btp_environment_id IS NULL)",
            name="single_btp_scope",
        ),
        Index("ix_agent_memories_owner_active_updated", "owner_user_id", "deleted_at", "updated_at"),
        Index("ix_agent_memories_owner_scope", "owner_user_id", "btp_account_id", "btp_environment_id"),
        Index("ix_agent_memories_expiry", "expires_at"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    btp_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_accounts.id", ondelete="RESTRICT")
    )
    btp_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_environments.id", ondelete="RESTRICT")
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source_investigation_reference: Mapped[str | None] = mapped_column(String(255))
    source_evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def scope_id(self) -> uuid.UUID | None:
        return self.btp_account_id or self.btp_environment_id
