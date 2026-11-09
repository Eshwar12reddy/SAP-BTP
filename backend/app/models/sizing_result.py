"""Owner-scoped persisted deterministic sizing results."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SizingResultRecord(Base):
    __tablename__ = "sizing_results"
    __table_args__ = (
        UniqueConstraint("result_id", name="uq_sizing_results_result_id"),
        Index("ix_sizing_results_owner_updated", "owner_user_id", "updated_at"),
        Index("ix_sizing_results_owner_scope", "owner_user_id", "btp_account_id", "btp_environment_id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    btp_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("btp_accounts.id", ondelete="RESTRICT"))
    btp_environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("btp_environments.id", ondelete="RESTRICT"))
    architecture_proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    architecture_proposal_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scenario_id: Mapped[str] = mapped_column(String(80), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(40), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requirements_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
