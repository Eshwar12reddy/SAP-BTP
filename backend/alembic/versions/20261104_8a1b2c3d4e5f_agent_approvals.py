"""durable proposal-only agent approvals

Revision ID: 8a1b2c3d4e5f
Revises: 7f24e8a1c9d0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8a1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "7f24e8a1c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("operation_type", sa.String(length=80), nullable=False),
        sa.Column("operation_summary", sa.Text(), nullable=False),
        sa.Column("canonical_args", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=20), server_default="high", nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("execution_available", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["decision_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_approvals_thread_status", "agent_approvals", ["thread_id", "status"])
    op.create_index("ix_agent_approvals_user_created", "agent_approvals", ["user_id", "created_at"])
    op.create_index("uq_agent_approvals_fingerprint", "agent_approvals", ["fingerprint"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_agent_approvals_fingerprint", table_name="agent_approvals")
    op.drop_index("ix_agent_approvals_user_created", table_name="agent_approvals")
    op.drop_index("ix_agent_approvals_thread_status", table_name="agent_approvals")
    op.drop_table("agent_approvals")
