"""agent replies

Additive only: two NULLable columns on ``messages`` plus keys. Existing rows are not
rewritten (both columns stay NULL). An assistant reply references the user message it
answers, in the same conversation, and each user message can have at most one reply.

Revision ID: c07606303f6e
Revises: 360b3bd3d310
Create Date: 2026-11-01 16:14:10.955402+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c07606303f6e'
down_revision: str | Sequence[str] | None = '360b3bd3d310'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('messages', sa.Column('reply_to_message_id', sa.UUID(), nullable=True))
    op.add_column(
        'messages',
        sa.Column('agent_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_unique_constraint(
        op.f('uq_messages_conversation_id_id'), 'messages', ['conversation_id', 'id']
    )
    op.create_unique_constraint(
        op.f('uq_messages_reply_to_message_id'), 'messages', ['reply_to_message_id']
    )
    op.create_foreign_key(
        'fk_messages_reply_to_message_id_messages',
        'messages',
        'messages',
        ['conversation_id', 'reply_to_message_id'],
        ['conversation_id', 'id'],
        ondelete='CASCADE',
    )
    # Autogenerate does not detect check constraints.
    op.create_check_constraint(
        op.f('ck_messages_reply_from_assistant'),
        'messages',
        "reply_to_message_id IS NULL OR role = 'assistant'",
    )


def downgrade() -> None:
    """Downgrade schema. Drops the reply links and agent metadata of assistant replies."""
    op.drop_constraint(op.f('ck_messages_reply_from_assistant'), 'messages', type_='check')
    op.drop_constraint('fk_messages_reply_to_message_id_messages', 'messages', type_='foreignkey')
    op.drop_constraint(op.f('uq_messages_reply_to_message_id'), 'messages', type_='unique')
    op.drop_constraint(op.f('uq_messages_conversation_id_id'), 'messages', type_='unique')
    op.drop_column('messages', 'agent_metadata')
    op.drop_column('messages', 'reply_to_message_id')
