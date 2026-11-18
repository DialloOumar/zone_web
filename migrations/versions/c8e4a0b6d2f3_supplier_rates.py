"""What a lessor is paid for each machine

- supplier_rates: dated price per machine, per worked unit or per month,
  append-only like client_rates.

Revision ID: c8e4a0b6d2f3
Revises: b7d3f9a5c1e2
Create Date: 2026-11-18
"""
from alembic import op
import sqlalchemy as sa

revision = 'c8e4a0b6d2f3'
down_revision = 'b7d3f9a5c1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('supplier_id', sa.Integer(), sa.ForeignKey('suppliers.id'), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id'), nullable=False),
        sa.Column('basis', sa.String(5), nullable=False, server_default='unit'),
        sa.Column('rate', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.String(10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
    )
    op.create_index('ix_supplier_rates_supplier_id', 'supplier_rates', ['supplier_id'])
    op.create_index('ix_supplier_rates_vehicle_id', 'supplier_rates', ['vehicle_id'])


def downgrade():
    op.drop_index('ix_supplier_rates_vehicle_id', table_name='supplier_rates')
    op.drop_index('ix_supplier_rates_supplier_id', table_name='supplier_rates')
    op.drop_table('supplier_rates')
