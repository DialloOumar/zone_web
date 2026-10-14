"""A supplier bill can concern several machines

Revision ID: c9f1b3d5e7a9
Revises: b8d0f2a4c6e7
Create Date: 2026-10-14

The one machine a bill carried moves to a link table that holds any number;
existing links are copied over, then the old column goes.
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9f1b3d5e7a9'
down_revision = 'b8d0f2a4c6e7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supplier_invoice_vehicles',
        sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('supplier_invoices.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', ondelete='CASCADE'), primary_key=True),
    )
    op.create_index('ix_supplier_invoice_vehicles_vehicle_id', 'supplier_invoice_vehicles', ['vehicle_id'])
    op.execute("INSERT INTO supplier_invoice_vehicles (invoice_id, vehicle_id) "
               "SELECT id, vehicle_id FROM supplier_invoices WHERE vehicle_id IS NOT NULL")
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.drop_index('ix_supplier_invoices_vehicle_id')
        batch_op.drop_constraint('fk_supplier_invoices_vehicle_id', type_='foreignkey')
        batch_op.drop_column('vehicle_id')


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.add_column(sa.Column('vehicle_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_supplier_invoices_vehicle_id', 'vehicles', ['vehicle_id'], ['id'])
        batch_op.create_index('ix_supplier_invoices_vehicle_id', ['vehicle_id'])
    op.execute("UPDATE supplier_invoices SET vehicle_id = (SELECT MIN(v.vehicle_id) FROM supplier_invoice_vehicles v "
               "WHERE v.invoice_id = supplier_invoices.id)")
    op.drop_index('ix_supplier_invoice_vehicles_vehicle_id', 'supplier_invoice_vehicles')
    op.drop_table('supplier_invoice_vehicles')
