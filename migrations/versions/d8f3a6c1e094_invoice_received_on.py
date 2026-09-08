"""The day a supplier's bill actually reached us

The invoice already carried the date the supplier wrote on it and the date it
falls due. Neither says when the paper arrived, and that is the one that tells
you a bill spent three weeks on somebody's desk before anyone recorded it.

Left empty on the bills already recorded: nobody wrote it down at the time, and
guessing it from the invoice's own date would invent a fact.

Revision ID: d8f3a6c1e094
Revises: c7e2b4d9f6a1
Create Date: 2026-09-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd8f3a6c1e094'
down_revision = 'c7e2b4d9f6a1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.add_column(sa.Column('received_on', sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch_op:
        batch_op.drop_column('received_on')
