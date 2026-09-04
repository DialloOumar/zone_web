"""Sites, cash accounts, and withdrawals

Three things the cashier's own account of her day asked for:

  * a cost can say where it was spent, from a short list she keeps, so "all of
    Siguiri in March" is a filter and not a search through free text;
  * money in and money out name an account — the boss's, the company's, an
    agent who fronts the cash when the box is empty — so each carries a running
    balance of what the box still owes it;
  * money can leave the box without being a cost. The boss taking his own money
    back spends nothing on the company's behalf.

`is_repayable` says how an account's balance reads: an advance is owed back,
what the company puts in is its own.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-09-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd3e4f5a6b7c8'
down_revision = 'c2d3e4f5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sites',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_sites_name'),
    )
    op.create_table(
        'cash_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('is_repayable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_cash_accounts_name'),
    )
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('site_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_expenses_site_id', 'sites', ['site_id'], ['id'])
    with op.batch_alter_table('cash_movements') as batch_op:
        batch_op.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_cash_movements_account_id', 'cash_accounts',
                                    ['account_id'], ['id'])


def downgrade():
    with op.batch_alter_table('cash_movements') as batch_op:
        batch_op.drop_constraint('fk_cash_movements_account_id', type_='foreignkey')
        batch_op.drop_column('account_id')
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_constraint('fk_expenses_site_id', type_='foreignkey')
        batch_op.drop_column('site_id')
    op.drop_table('cash_accounts')
    op.drop_table('sites')
