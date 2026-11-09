"""Create deterministic sizing result history.

Revision ID: d5f6a7b8c9d0
Revises: c4e5f6a7b8c9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d5f6a7b8c9d0"
down_revision: str | None = "c4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sizing_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("btp_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("btp_environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("architecture_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("architecture_proposal_revision", sa.Integer(), nullable=True),
        sa.Column("scenario_id", sa.String(length=80), nullable=False),
        sa.Column("formula_version", sa.String(length=40), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requirements_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["btp_account_id"], ["btp_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["btp_environment_id"], ["btp_environments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_id", name="uq_sizing_results_result_id"),
    )
    op.create_index("ix_sizing_results_owner_updated", "sizing_results", ["owner_user_id", "updated_at"])
    op.create_index("ix_sizing_results_owner_scope", "sizing_results", ["owner_user_id", "btp_account_id", "btp_environment_id"])


def downgrade() -> None:
    op.drop_index("ix_sizing_results_owner_scope", table_name="sizing_results")
    op.drop_index("ix_sizing_results_owner_updated", table_name="sizing_results")
    op.drop_table("sizing_results")
