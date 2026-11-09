"""Add owner-scoped immutable architecture proposal revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "8a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "architecture_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("btp_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("btp_environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("proposal_json", postgresql.JSONB(), nullable=False),
        sa.Column("requirements_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["btp_account_id"], ["btp_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_environment_id"], ["btp_environments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "revision", name="uq_architecture_proposals_revision"),
    )
    op.create_index("ix_architecture_proposals_owner_updated", "architecture_proposals", ["owner_user_id", "updated_at"])
    op.create_index("ix_architecture_proposals_owner_scope", "architecture_proposals", ["owner_user_id", "btp_account_id", "btp_environment_id"])


def downgrade() -> None:
    op.drop_index("ix_architecture_proposals_owner_scope", table_name="architecture_proposals")
    op.drop_index("ix_architecture_proposals_owner_updated", table_name="architecture_proposals")
    op.drop_table("architecture_proposals")
