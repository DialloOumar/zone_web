"""A supplier bill may be about one machine

Revision ID: f3a5b7c9d1e3
Revises: e2c4a6b8d0f1
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f3a5b7c9d1e3'
down_revision = 'e2c4a6b8d0f1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.add_column(sa.Column('vehicle_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_supplier_invoices_vehicle_id', 'vehicles', ['vehicle_id'], ['id'])
        batch_op.create_index('ix_supplier_invoices_vehicle_id', ['vehicle_id'])


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.drop_index('ix_supplier_invoices_vehicle_id')
        batch_op.drop_constraint('fk_supplier_invoices_vehicle_id', type_='foreignkey')
        batch_op.drop_column('vehicle_id')
