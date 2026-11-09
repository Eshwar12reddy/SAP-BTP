"""knowledge lexical index generations and parent/child chunks

Revision ID: a17b6c8d4e21
Revises: cc924a6aa7ae
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "a17b6c8d4e21"
down_revision: str | Sequence[str] | None = "cc924a6aa7ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add lexical and optional vector storage; requires vector preinstalled by an operator."""
    if not op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")):
        raise RuntimeError(
            "pgvector is not installed in this database. Enable it explicitly, then retry the migration."
        )
    op.create_table(
        "knowledge_index_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_validation_status", sa.String(16), server_default="validated", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config_version", sa.String(64), nullable=False),
        sa.Column("parent_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("child_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedding_model_id", sa.String(255), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=True),
        sa.Column("embedding_status", sa.String(16), server_default="disabled", nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('published', 'retired', 'failed')", name="ck_knowledge_index_generations_status_valid"),
        sa.CheckConstraint("version_validation_status = 'validated'", name="ck_knowledge_index_generations_version_validated"),
        sa.CheckConstraint("embedding_status IN ('disabled', 'ready', 'failed')", name="ck_knowledge_index_generations_embedding_status_valid"),
        sa.CheckConstraint("(embedding_status = 'disabled' AND embedding_model_id IS NULL AND embedding_dimensions IS NULL) OR (embedding_status IN ('ready', 'failed') AND embedding_model_id IS NOT NULL AND embedding_dimensions = 1536)", name="ck_knowledge_index_generations_embedding_identity_consistent"),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], name="fk_knowledge_index_generations_source_id_knowledge_sources", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id", "version_validation_status"],
            ["knowledge_source_versions.id", "knowledge_source_versions.source_id", "knowledge_source_versions.validation_status"],
            name="fk_knowledge_index_version_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_index_generations"),
        sa.UniqueConstraint("id", "source_id", "source_version_id", name="uq_index_gen_identity"),
    )
    op.create_index(
        "uq_knowledge_index_one_published_per_source",
        "knowledge_index_generations",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index("ix_knowledge_index_source_version", "knowledge_index_generations", ["source_id", "source_version_id"])

    op.create_table(
        "knowledge_parent_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_order", sa.Integer(), nullable=False),
        sa.Column("section_heading", sa.String(1000), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("source_offset_start", sa.Integer(), nullable=True),
        sa.Column("source_offset_end", sa.Integer(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id", "source_id", "source_version_id"],
            ["knowledge_index_generations.id", "knowledge_index_generations.source_id", "knowledge_index_generations.source_version_id"],
            name="fk_knowledge_parent_generation_identity",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_parent_chunks"),
        sa.UniqueConstraint("generation_id", "parent_order", name="uq_knowledge_parent_order"),
        sa.UniqueConstraint("id", "generation_id", "source_id", "source_version_id", name="uq_knowledge_parent_identity"),
    )
    op.create_index("ix_knowledge_parent_generation", "knowledge_parent_chunks", ["generation_id", "parent_order"])

    op.create_table(
        "knowledge_child_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_order", sa.Integer(), nullable=False),
        sa.Column("source_offset_start", sa.Integer(), nullable=True),
        sa.Column("source_offset_end", sa.Integer(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), sa.Computed("to_tsvector('english'::regconfig, coalesce(chunk_text, ''))", persisted=True), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_id", "source_id", "source_version_id"],
            ["knowledge_index_generations.id", "knowledge_index_generations.source_id", "knowledge_index_generations.source_version_id"],
            name="fk_knowledge_child_generation_identity",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "generation_id", "source_id", "source_version_id"],
            ["knowledge_parent_chunks.id", "knowledge_parent_chunks.generation_id", "knowledge_parent_chunks.source_id", "knowledge_parent_chunks.source_version_id"],
            name="fk_knowledge_child_parent_identity",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_child_chunks"),
        sa.UniqueConstraint("parent_id", "child_order", name="uq_knowledge_child_order"),
    )
    op.create_index("ix_knowledge_child_generation", "knowledge_child_chunks", ["generation_id", "child_order"])
    op.create_index("ix_knowledge_child_source_version", "knowledge_child_chunks", ["source_id", "source_version_id"])
    op.create_index("ix_knowledge_child_fts", "knowledge_child_chunks", ["search_vector"], postgresql_using="gin")


def downgrade() -> None:
    """Remove the derived index data and lexical indexes."""
    op.drop_index("ix_knowledge_child_fts", table_name="knowledge_child_chunks")
    op.drop_index("ix_knowledge_child_source_version", table_name="knowledge_child_chunks")
    op.drop_index("ix_knowledge_child_generation", table_name="knowledge_child_chunks")
    op.drop_table("knowledge_child_chunks")
    op.drop_index("ix_knowledge_parent_generation", table_name="knowledge_parent_chunks")
    op.drop_table("knowledge_parent_chunks")
    op.drop_index("ix_knowledge_index_source_version", table_name="knowledge_index_generations")
    op.drop_index("uq_knowledge_index_one_published_per_source", table_name="knowledge_index_generations")
    op.drop_table("knowledge_index_generations")
