"""Configured SAP BTP hierarchy: global account → subaccount → environment.

These rows are the *mapping* an administrator registers (``python -m app.cli btp ...``);
they are not discovered live. Environment names such as "Development" are labels only.
The hierarchy rules are enforced by the database through composite foreign keys.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountType(enum.StrEnum):
    GLOBAL_ACCOUNT = "global_account"
    SUBACCOUNT = "subaccount"


class RuntimeType(enum.StrEnum):
    CLOUD_FOUNDRY = "cloudfoundry"
    KYMA = "kyma"
    ABAP = "abap"
    NEO = "neo"


_RUNTIME_TYPES = ", ".join(f"'{runtime.value}'" for runtime in RuntimeType)


class BTPAccount(Base):
    __tablename__ = "btp_accounts"
    __table_args__ = (
        CheckConstraint(
            "account_type IN ('global_account', 'subaccount')", name="account_type_valid"
        ),
        # Global accounts are roots; every subaccount has exactly one global-account parent.
        CheckConstraint(
            "(account_type = 'global_account' AND parent_account_id IS NULL "
            "AND parent_account_type IS NULL) OR "
            "(account_type = 'subaccount' AND parent_account_id IS NOT NULL "
            "AND parent_account_type = 'global_account')",
            name="hierarchy_valid",
        ),
        # Target for composite foreign keys that pin the type of the referenced account.
        UniqueConstraint("id", "account_type", name="uq_btp_accounts_id_account_type"),
        ForeignKeyConstraint(
            ["parent_account_id", "parent_account_type"],
            ["btp_accounts.id", "btp_accounts.account_type"],
            name="fk_btp_accounts_parent_global_account",
            ondelete="RESTRICT",
        ),
        Index("ix_btp_accounts_parent_account_id", "parent_account_id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # The account GUID in SAP BTP.
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str | None] = mapped_column(String(50))
    parent_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    parent_account_type: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BTPEnvironment(Base):
    __tablename__ = "btp_environments"
    __table_args__ = (
        UniqueConstraint("btp_account_id", "name", name="uq_btp_environment_account_name"),
        CheckConstraint(f"runtime_type IN ({_RUNTIME_TYPES})", name="runtime_type_valid"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        # Environments live in subaccounts only.
        CheckConstraint("btp_account_type = 'subaccount'", name="in_subaccount"),
        ForeignKeyConstraint(
            ["btp_account_id", "btp_account_type"],
            ["btp_accounts.id", "btp_accounts.account_type"],
            name="fk_btp_environments_subaccount",
            ondelete="RESTRICT",
        ),
        Index("ix_btp_environments_btp_account_id", "btp_account_id"),
        Index(
            "uq_btp_environments_runtime_external_id",
            "runtime_type",
            "external_runtime_id",
            unique=True,
            postgresql_where=text("external_runtime_id IS NOT NULL"),
        ),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    btp_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    btp_account_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=AccountType.SUBACCOUNT.value
    )
    # A label (e.g. "Development"); meaningful only through this configured mapping.
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    runtime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # e.g. the Cloud Foundry org GUID or Kyma cluster id.
    external_runtime_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
