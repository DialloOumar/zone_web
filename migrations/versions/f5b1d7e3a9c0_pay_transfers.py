"""Paying the month: the bank transfer and the cash box's salary payments

- pay_runs: pay_account_id, transfer_on, transfer_total, transfer_reference,
  transfer_by -- the transfers once the bank has carried them out;
- cash_movements gets no column: a "salaire" movement uses staff_id and
  advance_month (the month paid).

Revision ID: f5b1d7e3a9c0
Revises: e4a0c6d2f8b9
Create Date: 2026-11-06
"""
from alembic import op
import sqlalchemy as sa

revision = 'f5b1d7e3a9c0'
down_revision = 'e4a0c6d2f8b9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pay_runs') as batch:
        batch.add_column(sa.Column('pay_account_id', sa.Integer()))
        batch.add_column(sa.Column('transfer_on', sa.String(10)))
        batch.add_column(sa.Column('transfer_total', sa.Integer()))
        batch.add_column(sa.Column('transfer_reference', sa.String(60)))
        batch.add_column(sa.Column('transfer_by', sa.Integer()))
        batch.create_foreign_key('fk_pay_runs_pay_account_id', 'cash_accounts', ['pay_account_id'], ['id'])
        batch.create_foreign_key('fk_pay_runs_transfer_by', 'users', ['transfer_by'], ['id'])


def downgrade():
    # The salaries the cash box paid go with the feature that wrote them.
    op.execute("DELETE FROM cash_movements WHERE kind = 'salaire'")
    with op.batch_alter_table('pay_runs') as batch:
        batch.drop_constraint('fk_pay_runs_transfer_by', type_='foreignkey')
        batch.drop_constraint('fk_pay_runs_pay_account_id', type_='foreignkey')
        batch.drop_column('transfer_by')
        batch.drop_column('transfer_reference')
        batch.drop_column('transfer_total')
        batch.drop_column('transfer_on')
        batch.drop_column('pay_account_id')
