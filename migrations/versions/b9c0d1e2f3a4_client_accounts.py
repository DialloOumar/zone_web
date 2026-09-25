"""Clients on the plan; receipts coded

A client carries his account under 4111; a receipt on his invoice carries
it too, for the sales journal.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b9c0d1e2f3a4'
down_revision = 'a8b9c0d1e2f3'
branch_labels = None
depends_on = None

TABLES = ('clients', 'client_payments')


def upgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('ledger_code', sa.String(length=12), nullable=True))
            batch_op.create_index(f'ix_{table}_ledger_code', ['ledger_code'])


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f'ix_{table}_ledger_code')
            batch_op.drop_column('ledger_code')
