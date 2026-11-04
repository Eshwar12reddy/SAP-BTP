"""A role granted to a user at one scope.

Scope integrity is enforced by the database:
- the scope is a real foreign key (account or environment), never a free-form id;
- for account scopes, ``scope_type`` must equal the referenced account's type;
- ``role_name`` is pinned to ``role_id`` so role/scope compatibility can be checked;
- at most one unrevoked grant exists per (user, role, scope).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NIL_UUID = "00000000-0000-0000-0000-000000000000"


class AccessGrant(Base):
    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('platform', 'global_account', 'subaccount', 'environment')",
            name="scope_type_valid",
        ),
        CheckConstraint(
            "(scope_type = 'platform' AND btp_account_id IS NULL "
            "AND btp_environment_id IS NULL AND NOT include_descendants) OR "
            "(scope_type IN ('global_account', 'subaccount') AND btp_account_id IS NOT NULL "
            "AND btp_environment_id IS NULL) OR "
            "(scope_type = 'environment' AND btp_environment_id IS NOT NULL "
            "AND btp_account_id IS NULL AND NOT include_descendants)",
            name="scope_shape_valid",
        ),
        # Administrators: platform or account scope. Knowledge roles: any scope (platform =
        # global knowledge only). Other roles: BTP scopes only.
        CheckConstraint(
            "(role_name = 'administrator' AND scope_type <> 'environment') OR "
            "(role_name IN ('knowledge_reader', 'knowledge_manager')) OR "
            "(role_name NOT IN ('administrator', 'knowledge_reader', 'knowledge_manager') "
            "AND scope_type <> 'platform')",
            name="role_scope_compatible",
        ),
        CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="expiry_valid"),
        ForeignKeyConstraint(
            ["role_id", "role_name"],
            ["roles.id", "roles.name"],
            name="fk_access_grants_role",
            ondelete="RESTRICT",
        ),
        # For account grants the declared scope type must match the account's real type.
        ForeignKeyConstraint(
            ["btp_account_id", "scope_type"],
            ["btp_accounts.id", "btp_accounts.account_type"],
            name="fk_access_grants_btp_account",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_access_grants_active",
            "user_id",
            "role_id",
            "scope_type",
            text(f"COALESCE(btp_account_id, btp_environment_id, '{NIL_UUID}'::uuid)"),
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_access_grants_user_id_unrevoked",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_access_grants_btp_account_id", "btp_account_id"),
        Index("ix_access_grants_btp_environment_id", "btp_environment_id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False)
    btp_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    btp_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_environments.id", ondelete="RESTRICT")
    )
    # Explicit opt-in: without it a grant covers exactly its own scope, never children.
    include_descendants: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def scope_id(self) -> uuid.UUID | None:
        return self.btp_account_id or self.btp_environment_id
