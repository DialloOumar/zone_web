"""A supplier invoice may settle a purchase order

Revision ID: a4c6e8b1d532
Revises: f3b9d5e7a420
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a4c6e8b1d532'
down_revision = 'f3b9d5e7a420'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.add_column(sa.Column('purchase_order_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_supplier_invoices_purchase_order', 'purchase_orders',
                                    ['purchase_order_id'], ['id'])
        batch_op.create_index('ix_supplier_invoices_purchase_order_id', ['purchase_order_id'])


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.drop_index('ix_supplier_invoices_purchase_order_id')
        batch_op.drop_constraint('fk_supplier_invoices_purchase_order', type_='foreignkey')
        batch_op.drop_column('purchase_order_id')
