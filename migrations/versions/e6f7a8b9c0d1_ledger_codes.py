"""A code from the plan on each money line

Caisse costs, supplier bills and client invoice lines each carry the account
they are coded to, for the journal the comptable imports. Empty to start;
filled by hand.

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e6f7a8b9c0d1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('expenses', 'supplier_invoices', 'client_invoice_lines'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('ledger_code', sa.String(length=12), nullable=True))
            batch_op.create_index(f'ix_{table}_ledger_code', ['ledger_code'])


def downgrade():
    for table in ('expenses', 'supplier_invoices', 'client_invoice_lines'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f'ix_{table}_ledger_code')
            batch_op.drop_column('ledger_code')
