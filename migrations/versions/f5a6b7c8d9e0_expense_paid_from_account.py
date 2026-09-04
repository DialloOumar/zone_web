"""A cost may be paid straight from an account, not from the box.

Two columns for one idea: the cost names the account that paid it, and the
money-in written to match points back at the cost. The pair keeps the box's
balance honest -- the money came in and went straight out -- and stops the
generated line being edited as if the cashier had entered it.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
"""
import sqlalchemy as sa
from alembic import op

revision = 'f5a6b7c8d9e0'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as b:
        b.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_expenses_account', 'cash_accounts',
                             ['account_id'], ['id'])
    with op.batch_alter_table('cash_movements') as b:
        b.add_column(sa.Column('expense_id', sa.Integer(), nullable=True))
        b.create_foreign_key('fk_cash_movements_expense', 'expenses',
                             ['expense_id'], ['id'])
        b.create_unique_constraint('uq_cash_movements_expense', ['expense_id'])


def downgrade():
    with op.batch_alter_table('cash_movements') as b:
        b.drop_constraint('uq_cash_movements_expense', type_='unique')
        b.drop_constraint('fk_cash_movements_expense', type_='foreignkey')
        b.drop_column('expense_id')
    with op.batch_alter_table('expenses') as b:
        b.drop_constraint('fk_expenses_account', type_='foreignkey')
        b.drop_column('account_id')
