"""step1 promos favorites reviews

Revision ID: 004
Revises: 003
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('promo_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('discount_type', sa.String(length=16), nullable=False),
        sa.Column('discount_value', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('min_order', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('max_uses', sa.Integer(), nullable=False),
        sa.Column('used_count', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_promo_codes_code'), 'promo_codes', ['code'], unique=True)

    op.create_table('favorites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_favorites_user_telegram_id'), 'favorites', ['user_telegram_id'], unique=False)

    op.create_table('reviews',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=True),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reviews_user_telegram_id'), 'reviews', ['user_telegram_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_reviews_user_telegram_id'), table_name='reviews')
    op.drop_table('reviews')
    op.drop_index(op.f('ix_favorites_user_telegram_id'), table_name='favorites')
    op.drop_table('favorites')
    op.drop_index(op.f('ix_promo_codes_code'), table_name='promo_codes')
    op.drop_table('promo_codes')
