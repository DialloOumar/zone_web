"""Bons de commande: purchase orders and their lines

What the store orders from a supplier, with the two signatures the company's
paper order carries (logistics, then finance), or a refusal with its reason.

Revision ID: c9e5a7b3d186
Revises: b8d4f6a2c975
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c9e5a7b3d186'
down_revision = 'b8d4f6a2c975'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'purchase_orders',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=20), nullable=False),
        sa.Column('supplier_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending_logistics'),
        sa.Column('requested_by', sa.Integer(), nullable=True),
        sa.Column('requester_name', sa.String(length=120), nullable=True),
        sa.Column('requester_phone', sa.String(length=60), nullable=True),
        sa.Column('logistics_by', sa.Integer(), nullable=True),
        sa.Column('logistics_at', sa.DateTime(), nullable=True),
        sa.Column('finance_by', sa.Integer(), nullable=True),
        sa.Column('finance_at', sa.DateTime(), nullable=True),
        sa.Column('rejected_by', sa.Integer(), nullable=True),
        sa.Column('rejected_at', sa.DateTime(), nullable=True),
        sa.Column('rejected_reason', sa.String(length=255), nullable=True),
        sa.Column('rejections', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_gross', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_discount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id']),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id']),
        sa.ForeignKeyConstraint(['logistics_by'], ['users.id']),
        sa.ForeignKeyConstraint(['finance_by'], ['users.id']),
        sa.ForeignKeyConstraint(['rejected_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number', name='uq_purchase_orders_number'),
    )
    op.create_index('ix_purchase_orders_supplier_id', 'purchase_orders', ['supplier_id'])
    op.create_table(
        'purchase_order_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('part_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(length=160), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('unit_price', sa.Integer(), nullable=False),
        sa.Column('discount', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['purchase_orders.id']),
        sa.ForeignKeyConstraint(['part_id'], ['parts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_purchase_order_lines_order_id', 'purchase_order_lines', ['order_id'])


def downgrade():
    op.drop_index('ix_purchase_order_lines_order_id', table_name='purchase_order_lines')
    op.drop_table('purchase_order_lines')
    op.drop_index('ix_purchase_orders_supplier_id', table_name='purchase_orders')
    op.drop_table('purchase_orders')
