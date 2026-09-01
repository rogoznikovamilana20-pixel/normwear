"""step2 shipments cart reminders

Revision ID: 005
Revises: 004
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('shipments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('carrier', sa.String(length=32), nullable=False),
        sa.Column('tracking_number', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shipments_order_id'), 'shipments', ['order_id'], unique=False)

    op.create_table('cart_reminders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('product_ids_json', sa.Text(), nullable=False),
        sa.Column('reminded', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cart_reminders_user_telegram_id'), 'cart_reminders', ['user_telegram_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_cart_reminders_user_telegram_id'), table_name='cart_reminders')
    op.drop_table('cart_reminders')
    op.drop_index(op.f('ix_shipments_order_id'), table_name='shipments')
    op.drop_table('shipments')
