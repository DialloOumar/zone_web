"""An order line can name the machines it is for

Revision ID: e2b4d6f8a0c3
Revises: d1a3c5e7f9b2
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = 'e2b4d6f8a0c3'
down_revision = 'd1a3c5e7f9b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'purchase_order_line_vehicles',
        sa.Column('line_id', sa.Integer(), sa.ForeignKey('purchase_order_lines.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('ix_purchase_order_line_vehicles_vehicle_id', 'purchase_order_line_vehicles', ['vehicle_id'])


def downgrade():
    op.drop_index('ix_purchase_order_line_vehicles_vehicle_id', 'purchase_order_line_vehicles')
    op.drop_table('purchase_order_line_vehicles')
