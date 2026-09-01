"""step3 referrals + segments

Revision ID: 006
Revises: 005
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('referrals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('referrer_id', sa.BigInteger(), nullable=False),
        sa.Column('referred_id', sa.BigInteger(), nullable=False),
        sa.Column('bonus_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_referrals_referrer_id'), 'referrals', ['referrer_id'], unique=False)
    op.create_index(op.f('ix_referrals_referred_id'), 'referrals', ['referred_id'], unique=True)

    op.create_table('referral_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bonus_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade() -> None:
    op.drop_table('referral_config')
    op.drop_index(op.f('ix_referrals_referred_id'), table_name='referrals')
    op.drop_index(op.f('ix_referrals_referrer_id'), table_name='referrals')
    op.drop_table('referrals')
