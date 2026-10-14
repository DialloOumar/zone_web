"""A cash cost can be split over several machines

Revision ID: d1a3c5e7f9b2
Revises: c9f1b3d5e7a9
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = 'd1a3c5e7f9b2'
down_revision = 'c9f1b3d5e7a9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'expense_shares',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('expense_id', sa.Integer(), sa.ForeignKey('expenses.id', ondelete='CASCADE'), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
    )
    op.create_index('ix_expense_shares_expense_id', 'expense_shares', ['expense_id'])
    op.create_index('ix_expense_shares_vehicle_id', 'expense_shares', ['vehicle_id'])


def downgrade():
    op.drop_index('ix_expense_shares_vehicle_id', 'expense_shares')
    op.drop_index('ix_expense_shares_expense_id', 'expense_shares')
    op.drop_table('expense_shares')
