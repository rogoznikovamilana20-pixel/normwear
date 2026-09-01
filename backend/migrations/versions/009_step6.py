"""step6 admin audit log

Revision ID: 009
Revises: 008
Create Date: 2026-09-01

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '009'
down_revision: Union[str, None] = '008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table('admin_audit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_id', sa.BigInteger(), nullable=False),
        sa.Column('admin_name', sa.String(length=128), nullable=True),
        sa.Column('action', sa.String(length=255), nullable=False),
        sa.Column('target', sa.String(length=255), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_audit_admin_id'), 'admin_audit', ['admin_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_admin_audit_admin_id'), table_name='admin_audit')
    op.drop_table('admin_audit')
