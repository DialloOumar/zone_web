"""An instalment on a supplier's bill names the account it was paid from

A payment entered on the invoice said when, how much and how -- never from
where. Now it may name the account: the company's bank account, the Orange
Money line, the boss's own. The same list of accounts the cash box uses, so an
account is named once for the whole app.

A label only. Nothing here touches the cash box's balance or what the box owes
an account: the box's own payments are entered on the Dépenses page and reach
the bill from there, as before.

Revision ID: b4f7a2c9e615
Revises: a9d3e6f1c482
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b4f7a2c9e615'
down_revision = 'a9d3e6f1c482'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_payments') as batch_op:
        batch_op.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_supplier_payments_account_id', 'cash_accounts',
                                    ['account_id'], ['id'])
    op.create_index('ix_supplier_payments_account_id', 'supplier_payments',
                    ['account_id'])


def downgrade():
    op.drop_index('ix_supplier_payments_account_id', table_name='supplier_payments')
    with op.batch_alter_table('supplier_payments') as batch_op:
        batch_op.drop_constraint('fk_supplier_payments_account_id', type_='foreignkey')
        batch_op.drop_column('account_id')
