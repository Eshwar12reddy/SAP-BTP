"""reply status

Additive only: two NULLable columns on ``messages`` and one check constraint. Existing
rows are not rewritten. A user message records whether an agent reply was requested and
how that attempt ended, so a failed or unfinished reply survives a page refresh and a
reply can be claimed atomically across worker processes.

Revision ID: 5d8e2a7c41b9
Revises: c07606303f6e
Create Date: 2026-11-01 17:20:00.000000+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5d8e2a7c41b9'
down_revision: str | Sequence[str] | None = 'c07606303f6e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('messages', sa.Column('reply_status', sa.String(length=16), nullable=True))
    op.add_column(
        'messages', sa.Column('reply_status_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f('ck_messages_reply_status_valid'),
        'messages',
        "(reply_status IS NULL AND reply_status_at IS NULL) OR "
        "(role = 'user' AND reply_status IN ('generating', 'failed', 'completed') "
        "AND reply_status_at IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema. Drops the recorded reply states."""
    op.drop_constraint(op.f('ck_messages_reply_status_valid'), 'messages', type_='check')
    op.drop_column('messages', 'reply_status_at')
    op.drop_column('messages', 'reply_status')
