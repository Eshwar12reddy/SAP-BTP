"""Create immutable infrastructure plan revisions.

Revision ID: e7f8a9b0c1d2
Revises: d5f6a7b8c9d0
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "infrastructure_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("btp_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("btp_environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("architecture_proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("architecture_proposal_revision", sa.Integer(), nullable=False),
        sa.Column("sizing_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("output_format", sa.String(length=30), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requirements_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["btp_account_id"], ["btp_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_environment_id"], ["btp_environments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "revision", name="uq_infrastructure_plans_revision"),
    )
    op.create_index("ix_infrastructure_plans_owner_updated", "infrastructure_plans", ["owner_user_id", "updated_at"])
    op.create_index("ix_infrastructure_plans_owner_scope", "infrastructure_plans", ["owner_user_id", "btp_account_id", "btp_environment_id"])


def downgrade() -> None:
    op.drop_index("ix_infrastructure_plans_owner_scope", table_name="infrastructure_plans")
    op.drop_index("ix_infrastructure_plans_owner_updated", table_name="infrastructure_plans")
    op.drop_table("infrastructure_plans")
