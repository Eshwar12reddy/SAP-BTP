"""Durable human approvals for proposal-only agent investigations."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    EXECUTION_UNAVAILABLE = "execution_unavailable"


class ApprovalDecision(enum.StrEnum):
    EXECUTE = "execute"
    CANCEL = "cancel"
    OTHER_APPROACH = "other_approach"
    EXPLAIN_COMMAND = "explain_command"


class AgentApproval(Base):
    """A redacted, exact proposal tied to one authorized LangGraph thread.

    This table deliberately has no command/executor fields.  The current phase can
    record and explain proposals, but cannot change SAP BTP or execute shell commands.
    """

    __tablename__ = "agent_approvals"
    __table_args__ = (
        Index("ix_agent_approvals_thread_status", "thread_id", "status"),
        Index("ix_agent_approvals_user_created", "user_id", "created_at"),
        Index("uq_agent_approvals_fingerprint", "fingerprint", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    operation_summary: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_args: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="high")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=ApprovalStatus.PENDING.value
    )
    execution_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decision: Mapped[str | None] = mapped_column(String(32))
    result_summary: Mapped[str | None] = mapped_column(Text)

