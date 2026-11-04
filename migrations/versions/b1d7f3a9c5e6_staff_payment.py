"""How an employee is paid: transfer, Orange Money or cash, and the account

- banks: the list of banks, one spelling each, with the bank code;
- staff: pay_method, bank_id, bank_branch, bank_account, bank_key,
  mobile_money.

Empty to start with: the banks and accounts are typed in by hand.

Revision ID: b1d7f3a9c5e6
Revises: a9c5e1f7b3d4
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'b1d7f3a9c5e6'
down_revision = 'a9c5e1f7b3d4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'banks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('code', sa.String(10)),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
    )
    with op.batch_alter_table('staff') as batch:
        batch.add_column(sa.Column('pay_method', sa.String(12)))
        batch.add_column(sa.Column('bank_id', sa.Integer()))
        batch.add_column(sa.Column('bank_branch', sa.String(10)))
        batch.add_column(sa.Column('bank_account', sa.String(30)))
        batch.add_column(sa.Column('bank_key', sa.String(4)))
        batch.add_column(sa.Column('mobile_money', sa.String(30)))
        batch.create_foreign_key('fk_staff_bank_id', 'banks', ['bank_id'], ['id'])


def downgrade():
    with op.batch_alter_table('staff') as batch:
        batch.drop_constraint('fk_staff_bank_id', type_='foreignkey')
        batch.drop_column('mobile_money')
        batch.drop_column('bank_key')
        batch.drop_column('bank_account')
        batch.drop_column('bank_branch')
        batch.drop_column('bank_id')
        batch.drop_column('pay_method')
    op.drop_table('banks')
