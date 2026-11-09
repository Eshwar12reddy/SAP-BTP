"""Add isolated, authorized long-term agent memories.

The table is additive and is created only by an explicit Alembic migration.  Normal
application startup does not initialize it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7f24e8a1c9d0"
down_revision: str | Sequence[str] | None = "d9f34c1b7a62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("btp_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("btp_environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_investigation_reference", sa.String(length=255), nullable=True),
        sa.Column("source_evidence", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('preference', 'verified_finding', 'resource_reference', 'incident_pattern')",
            name="category_valid",
        ),
        sa.CheckConstraint(
            "verification_status IN ('confirmed', 'user_confirmed', 'hypothesis', 'outdated', 'rejected')",
            name="verification_status_valid",
        ),
        sa.CheckConstraint("char_length(btrim(content)) > 0", name="content_not_blank"),
        sa.CheckConstraint("char_length(content) <= 8000", name="content_bounded"),
        sa.CheckConstraint("schema_version > 0", name="schema_version_positive"),
        sa.CheckConstraint("revision > 0", name="revision_positive"),
        sa.CheckConstraint("btp_account_id IS NULL OR btp_environment_id IS NULL", name="single_btp_scope"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_account_id"], ["btp_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_environment_id"], ["btp_environments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_memories_owner_active_updated",
        "agent_memories",
        ["owner_user_id", "deleted_at", "updated_at"],
    )
    op.create_index(
        "ix_agent_memories_owner_scope",
        "agent_memories",
        ["owner_user_id", "btp_account_id", "btp_environment_id"],
    )
    op.create_index("ix_agent_memories_expiry", "agent_memories", ["expires_at"])
    # A retry or concurrent request must not create the same active memory twice.
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_memories_active_dedupe ON agent_memories "
        "(owner_user_id, category, COALESCE(btp_account_id, '00000000-0000-0000-0000-000000000000'::uuid), "
        "COALESCE(btp_environment_id, '00000000-0000-0000-0000-000000000000'::uuid), content_hash) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_agent_memories_active_dedupe", table_name="agent_memories")
    op.drop_index("ix_agent_memories_expiry", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_scope", table_name="agent_memories")
    op.drop_index("ix_agent_memories_owner_active_updated", table_name="agent_memories")
    op.drop_table("agent_memories")
