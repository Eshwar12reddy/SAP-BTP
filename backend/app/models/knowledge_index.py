"""Published lexical indexes for immutable knowledge document versions."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IndexStatus(enum.StrEnum):
    PUBLISHED = "published"
    RETIRED = "retired"
    FAILED = "failed"


class KnowledgeIndexGeneration(Base):
    __tablename__ = "knowledge_index_generations"
    __table_args__ = (
        CheckConstraint("status IN ('published', 'retired', 'failed')", name="status_valid"),
        CheckConstraint(
            "version_validation_status = 'validated'", name="version_validated"
        ),
        CheckConstraint(
            "embedding_status IN ('disabled', 'ready', 'failed')", name="embedding_status_valid"
        ),
        CheckConstraint(
            "(embedding_status = 'disabled' AND embedding_model_id IS NULL AND embedding_dimensions IS NULL) OR "
            "(embedding_status IN ('ready', 'failed') AND embedding_model_id IS NOT NULL AND embedding_dimensions = 1536)",
            name="embedding_identity_consistent",
        ),
        ForeignKeyConstraint(
            ["source_version_id", "source_id", "version_validation_status"],
            [
                "knowledge_source_versions.id",
                "knowledge_source_versions.source_id",
                "knowledge_source_versions.validation_status",
            ],
            name="fk_knowledge_index_version_source",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "source_id", "source_version_id", name="uq_index_gen_identity"),
        Index(
            "uq_knowledge_index_one_published_per_source",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index("ix_knowledge_index_source_version", "source_id", "source_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_validation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="validated"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    child_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    embedding_model_id: Mapped[str | None] = mapped_column(String(255))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="disabled")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeParentChunk(Base):
    __tablename__ = "knowledge_parent_chunks"
    __table_args__ = (
        UniqueConstraint(
            "generation_id", "parent_order", name="uq_knowledge_parent_order"
        ),
        UniqueConstraint(
            "id",
            "generation_id",
            "source_id",
            "source_version_id",
            name="uq_knowledge_parent_identity",
        ),
        ForeignKeyConstraint(
            ["generation_id", "source_id", "source_version_id"],
            [
                "knowledge_index_generations.id",
                "knowledge_index_generations.source_id",
                "knowledge_index_generations.source_version_id",
            ],
            name="fk_knowledge_parent_generation_identity",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_parent_generation", "generation_id", "parent_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_order: Mapped[int] = mapped_column(Integer, nullable=False)
    section_heading: Mapped[str | None] = mapped_column(String(1000))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    source_offset_start: Mapped[int | None] = mapped_column(Integer)
    source_offset_end: Mapped[int | None] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeChildChunk(Base):
    __tablename__ = "knowledge_child_chunks"
    __table_args__ = (
        UniqueConstraint(
            "parent_id", "child_order", name="uq_knowledge_child_order"
        ),
        ForeignKeyConstraint(
            ["generation_id", "source_id", "source_version_id"],
            [
                "knowledge_index_generations.id",
                "knowledge_index_generations.source_id",
                "knowledge_index_generations.source_version_id",
            ],
            name="fk_knowledge_child_generation_identity",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_id", "generation_id", "source_id", "source_version_id"],
            [
                "knowledge_parent_chunks.id",
                "knowledge_parent_chunks.generation_id",
                "knowledge_parent_chunks.source_id",
                "knowledge_parent_chunks.source_version_id",
            ],
            name="fk_knowledge_child_parent_identity",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_child_generation", "generation_id", "child_order"),
        Index("ix_knowledge_child_source_version", "source_id", "source_version_id"),
        Index("ix_knowledge_child_fts", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    child_order: Mapped[int] = mapped_column(Integer, nullable=False)
    source_offset_start: Mapped[int | None] = mapped_column(Integer)
    source_offset_end: Mapped[int | None] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    search_vector: Mapped[object] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english'::regconfig, coalesce(chunk_text, ''))", persisted=True),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
