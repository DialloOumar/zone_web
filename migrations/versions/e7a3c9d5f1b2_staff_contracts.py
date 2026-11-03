"""Employees' contracts: kind, dates, base salary

One in force at a time per employee; a new one closes the one before. The
signed paper may be kept beside it. Empty to start with.

Revision ID: e7a3c9d5f1b2
Revises: d6f2b8c4e0a1
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7a3c9d5f1b2'
down_revision = 'd6f2b8c4e0a1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'staff_contracts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('start_date', sa.String(10), nullable=False),
        sa.Column('end_date', sa.String(10)),
        sa.Column('trial_end', sa.String(10)),
        sa.Column('base_salary', sa.Integer()),
        sa.Column('ended_on', sa.String(10)),
        sa.Column('end_reason', sa.String(20)),
        sa.Column('file_key', sa.String(200)),
        sa.Column('file_type', sa.String(60)),
        sa.Column('note', sa.String(255)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_staff_contracts_staff_id', 'staff_contracts', ['staff_id'])


def downgrade():
    op.drop_index('ix_staff_contracts_staff_id', 'staff_contracts')
    op.drop_table('staff_contracts')
