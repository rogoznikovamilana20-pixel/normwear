"""support tickets

Revision ID: 003
Revises: 002
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('support_tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('admin_chat_id', sa.BigInteger(), nullable=False),
        sa.Column('admin_message_id', sa.Integer(), nullable=False),
        sa.Column('user_message_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_support_tickets_user_telegram_id'), 'support_tickets', ['user_telegram_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_support_tickets_user_telegram_id'), table_name='support_tickets')
    op.drop_table('support_tickets')
