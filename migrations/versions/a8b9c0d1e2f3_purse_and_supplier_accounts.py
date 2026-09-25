"""Purses and suppliers on the plan; settlements coded

A purse carries the treasury (or tiers) account it is; a supplier carries
his account under 4011; a bill's instalment carries the supplier's account
at the time it was paid.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a8b9c0d1e2f3'
down_revision = 'f7a8b9c0d1e2'
branch_labels = None
depends_on = None

TABLES = ('cash_accounts', 'suppliers', 'supplier_payments')


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
