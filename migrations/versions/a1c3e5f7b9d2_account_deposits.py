"""Money put on the company's accounts, and where each account started

A starting balance on each company account, and a line for money that
lands on one without a client bill behind it.

Revision ID: a1c3e5f7b9d2
Revises: d0f2a4b6c8e0
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c3e5f7b9d2'
down_revision = 'd0f2a4b6c8e0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cash_accounts') as batch:
        batch.add_column(sa.Column('opening_balance', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('opening_date', sa.String(length=10), nullable=True))
    op.create_table(
        'account_deposits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('cash_accounts.id'), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=5), nullable=False, server_default='GNF'),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('source', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('ledger_code', sa.String(length=12), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_account_deposits_account_id', 'account_deposits', ['account_id'])
    op.create_index('ix_account_deposits_ledger_code', 'account_deposits', ['ledger_code'])


def downgrade():
    op.drop_index('ix_account_deposits_ledger_code', table_name='account_deposits')
    op.drop_index('ix_account_deposits_account_id', table_name='account_deposits')
    op.drop_table('account_deposits')
    with op.batch_alter_table('cash_accounts') as batch:
        batch.drop_column('opening_date')
        batch.drop_column('opening_balance')
