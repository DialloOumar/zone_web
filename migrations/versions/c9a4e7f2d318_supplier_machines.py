"""A supplier may lease machines to the company, and a machine names its lessor

Some suppliers rent engines to the company and bill it on the hours and trips
those engines work -- the very hours and trips the daily entries record. A
supplier can now be marked as one, and its machines attached to it, so the
entries of its machines can be pulled up and printed under its name to check
the bill it sends.

A machine has one lessor at a time, so the link sits on the vehicle.

Revision ID: c9a4e7f2d318
Revises: b4f7a2c9e615
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c9a4e7f2d318'
down_revision = 'b4f7a2c9e615'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.add_column(sa.Column('provides_machines', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('supplier_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_vehicles_supplier_id', 'suppliers',
                                    ['supplier_id'], ['id'])
    op.create_index('ix_vehicles_supplier_id', 'vehicles', ['supplier_id'])


def downgrade():
    op.drop_index('ix_vehicles_supplier_id', table_name='vehicles')
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_constraint('fk_vehicles_supplier_id', type_='foreignkey')
        batch_op.drop_column('supplier_id')
    with op.batch_alter_table('suppliers') as batch_op:
        batch_op.drop_column('provides_machines')
