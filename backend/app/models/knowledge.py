"""Knowledge sources (logical documents) and their immutable versions (uploaded files).

Rules enforced by PostgreSQL, so no code path or race can break them:
- A source's scope is ``global`` (no BTP reference), one BTP account or one BTP
  environment, and the reference is a real foreign key.
- Every source row points at an active version that belongs to the same source and is
  ``validated`` (NOT NULL plus a composite foreign key onto ``(id, source_id,
  validation_status)``). There is never a source without a validated document.
- Version numbers are unique per source, so concurrent uploads cannot share a number.
- Owner, creator, uploader and remover are user foreign keys with RESTRICT, so history
  cannot be orphaned (users are disabled, never deleted).
- A removed source records who removed it, when, and why.

Insert order: ``versions.source_id`` is checked at commit (DEFERRABLE INITIALLY DEFERRED),
so registration inserts version 1, then the source that points at it, in one transaction.

A trigger (see the migration) makes version rows immutable: only ``validation_status`` and
``validated_at`` may change, only away from ``pending``, and rows cannot be deleted.
Stored file bytes are protected by the storage layer, not by these columns.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeScope(enum.StrEnum):
    GLOBAL = "global"
    BTP_ACCOUNT = "btp_account"
    BTP_ENVIRONMENT = "btp_environment"


class SourceStatus(enum.StrEnum):
    ACTIVE = "active"
    REMOVED = "removed"


class ValidationStatus(enum.StrEnum):
    # "pending" is reserved for asynchronous checks (e.g. a future malware scanner) and is
    # never treated as validated: the active-version key only accepts "validated".
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"


class KnowledgeMediaType(enum.StrEnum):
    PDF = "application/pdf"
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"


TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 2000
MAX_TAGS = 20
TAG_MAX_LENGTH = 50
FILENAME_MAX_LENGTH = 255
NOTE_MAX_LENGTH = 500


def _values(choices: type[enum.StrEnum]) -> str:
    return ", ".join(f"'{choice.value}'" for choice in choices)


def _user_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="RESTRICT")


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        CheckConstraint(f"scope_type IN ({_values(KnowledgeScope)})", name="scope_type_valid"),
        CheckConstraint(
            "(scope_type = 'global' AND btp_account_id IS NULL "
            "AND btp_environment_id IS NULL) OR "
            "(scope_type = 'btp_account' AND btp_account_id IS NOT NULL "
            "AND btp_environment_id IS NULL) OR "
            "(scope_type = 'btp_environment' AND btp_environment_id IS NOT NULL "
            "AND btp_account_id IS NULL)",
            name="scope_shape_valid",
        ),
        CheckConstraint(f"status IN ({_values(SourceStatus)})", name="status_valid"),
        CheckConstraint(
            "(status = 'active' AND removed_at IS NULL AND removed_by IS NULL "
            "AND removal_reason IS NULL) OR "
            "(status = 'removed' AND removed_at IS NOT NULL AND removed_by IS NOT NULL "
            "AND removal_reason IS NOT NULL)",
            name="removal_recorded",
        ),
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(f"cardinality(tags) <= {MAX_TAGS}", name="tags_bounded"),
        CheckConstraint("active_version_status = 'validated'", name="active_version_validated"),
        CheckConstraint("revision > 0", name="revision_positive"),
        # The active version is one of this source's own versions, and validated.
        # use_alter: the two tables reference each other.
        ForeignKeyConstraint(
            ["active_version_id", "id", "active_version_status"],
            [
                "knowledge_source_versions.id",
                "knowledge_source_versions.source_id",
                "knowledge_source_versions.validation_status",
            ],
            name="fk_knowledge_sources_active_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        Index("ix_knowledge_sources_owner_id", "owner_id"),
        Index("ix_knowledge_sources_btp_account_id", "btp_account_id"),
        Index("ix_knowledge_sources_btp_environment_id", "btp_environment_id"),
        Index("ix_knowledge_sources_status_created_at", "status", "created_at"),
        Index("ix_knowledge_sources_tags", "tags", postgresql_using="gin"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX_LENGTH))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(TAG_MAX_LENGTH)), nullable=False, server_default=text("'{}'")
    )
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    btp_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_accounts.id", ondelete="RESTRICT")
    )
    btp_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("btp_environments.id", ondelete="RESTRICT")
    )
    # Set from the authenticated user at registration; never taken from a request body.
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), _user_fk(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), _user_fk(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=SourceStatus.ACTIVE.value
    )
    active_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Always 'validated'; it is part of the composite key that proves the active version is.
    active_version_status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Optimistic concurrency: a metadata update must name the revision it was based on.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), _user_fk())
    removal_reason: Mapped[str | None] = mapped_column(String(NOTE_MAX_LENGTH))

    @property
    def scope_id(self) -> uuid.UUID | None:
        return self.btp_account_id or self.btp_environment_id


class KnowledgeSourceVersion(Base):
    __tablename__ = "knowledge_source_versions"
    __table_args__ = (
        # Two concurrent uploads cannot both become "version 3".
        UniqueConstraint("source_id", "version_number", name="uq_knowledge_versions_source_number"),
        # Target of the active-version composite foreign key.
        UniqueConstraint(
            "id", "source_id", "validation_status", name="uq_knowledge_versions_id_source_status"
        ),
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint(f"media_type IN ({_values(KnowledgeMediaType)})", name="media_type_valid"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        CheckConstraint("storage_key ~ '^[0-9a-f]{32}$'", name="storage_key_format"),
        CheckConstraint(
            f"validation_status IN ({_values(ValidationStatus)})", name="validation_status_valid"
        ),
        CheckConstraint(
            "(validation_status = 'pending') = (validated_at IS NULL)", name="validation_timestamp"
        ),
        CheckConstraint("char_length(btrim(original_filename)) > 0", name="filename_not_blank"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Checked at commit, so version 1 can be inserted before the source that points at it.
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_sources.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Display name only (sanitized). Never used as a filesystem path.
    original_filename: Mapped[str] = mapped_column(String(FILENAME_MAX_LENGTH), nullable=False)
    # Detected from the content, not from the client's extension or Content-Type.
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Server-generated opaque key (32 hex characters). Never returned by the API.
    storage_key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), _user_fk(), nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ValidationStatus.PENDING.value
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    change_note: Mapped[str | None] = mapped_column(String(NOTE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
