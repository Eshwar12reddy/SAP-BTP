"""create conversations and messages

Initial, purely additive schema for persistent chat history.

Revision ID: bf219527df9c
Revises: 
Create Date: 2026-11-01 14:18:58.016308+00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bf219527df9c'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('conversations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('title', sa.String(length=200), server_default='New conversation', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(btrim(title)) > 0', name=op.f('ck_conversations_title_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index('ix_conversations_updated_at_id', 'conversations', ['updated_at', 'id'], unique=False)
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('client_message_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('user', 'assistant', 'system')", name=op.f('ck_messages_role_valid')),
    sa.CheckConstraint('char_length(content) > 0', name=op.f('ck_messages_content_not_empty')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_messages_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    sa.UniqueConstraint('conversation_id', 'client_message_id', name=op.f('uq_messages_conversation_id_client_message_id'))
    )
    op.create_index('ix_messages_conversation_id_seq', 'messages', ['conversation_id', 'seq'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_messages_conversation_id_seq', table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_conversations_updated_at_id', table_name='conversations')
    op.drop_table('conversations')
