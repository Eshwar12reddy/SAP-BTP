"""Non-secret SAP BTP connection metadata and per-user active selections."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConnectionValidationResult(enum.StrEnum):
    NOT_VALIDATED = "not_validated"
    VALID_CONFIGURATION = "valid_configuration"
    VALIDATED = "validated"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    TLS_FAILURE = "tls_failure"
    UNSUPPORTED_RUNTIME = "unsupported_runtime"
    UNSUPPORTED_AUTHENTICATION = "unsupported_authentication"
    CONNECTION_DISABLED = "connection_disabled"
    INVALID_CONFIGURATION = "invalid_configuration"


class BTPConnection(Base):
    __tablename__ = "btp_connections"
    __table_args__ = (
        UniqueConstraint("btp_environment_id", name="uq_btp_connections_environment"),
        CheckConstraint("char_length(btrim(display_name)) > 0", name="display_name_not_blank"),
        CheckConstraint("char_length(btrim(api_endpoint)) > 0", name="api_endpoint_not_blank"),
        CheckConstraint("char_length(btrim(credential_reference)) > 0", name="credential_reference_not_blank"),
        CheckConstraint("char_length(btrim(authentication_method)) > 0", name="authentication_method_not_blank"),
        CheckConstraint("revision > 0", name="revision_positive"),
        Index("ix_btp_connections_account_id", "btp_account_id"),
        Index("ix_btp_connections_environment_enabled", "btp_environment_id", "enabled"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    btp_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    btp_environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_environments.id", ondelete="RESTRICT"), nullable=False
    )
    api_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Opaque reference only. Secret material is resolved by trusted backend code.
    credential_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    authentication_method: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_validation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_validation_result: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=ConnectionValidationResult.NOT_VALIDATED.value
    )
    last_validation_error_category: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BTPActiveConnection(Base):
    __tablename__ = "btp_active_connections"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_connections.id", ondelete="CASCADE"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
