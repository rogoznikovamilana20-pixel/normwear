"""banned products

Revision ID: 002
Revises: 001
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('banned_products',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sku', sa.String(length=128), nullable=True),
        sa.Column('title_pattern', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_banned_products_sku'), 'banned_products', ['sku'], unique=True)
    op.create_index(op.f('ix_banned_products_title_pattern'), 'banned_products', ['title_pattern'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_banned_products_title_pattern'), table_name='banned_products')
    op.drop_index(op.f('ix_banned_products_sku'), table_name='banned_products')
    op.drop_table('banned_products')
