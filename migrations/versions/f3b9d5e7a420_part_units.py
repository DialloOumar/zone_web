"""Units the store counts in, kept by the store

Revision ID: f3b9d5e7a420
Revises: e2a8c4d6f319
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f3b9d5e7a420'
down_revision = 'e2a8c4d6f319'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'part_units',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=40), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_part_units_code'),
        sa.UniqueConstraint('name', name='uq_part_units_name'),
    )


def downgrade():
    op.drop_table('part_units')
