"""Transfers between company accounts, and the bank's fee on a transfer

Revision ID: e5a7c9d1f3b4
Revises: d4f6b8c0e2a3
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5a7c9d1f3b4'
down_revision = 'd4f6b8c0e2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'account_transfers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('cash_accounts.id'), nullable=False),
        sa.Column('to_account_id', sa.Integer(), sa.ForeignKey('cash_accounts.id'), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('fee_charge_id', sa.Integer(), sa.ForeignKey('bank_charges.id'), nullable=True, unique=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_account_transfers_account_id', 'account_transfers', ['account_id'])
    op.create_index('ix_account_transfers_to_account_id', 'account_transfers', ['to_account_id'])
    with op.batch_alter_table('cash_transfers') as batch:
        batch.add_column(sa.Column('fee_charge_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_cash_transfers_fee_charge_id', 'bank_charges', ['fee_charge_id'], ['id'])
        batch.create_unique_constraint('uq_cash_transfers_fee_charge_id', ['fee_charge_id'])


def downgrade():
    with op.batch_alter_table('cash_transfers') as batch:
        batch.drop_constraint('uq_cash_transfers_fee_charge_id', type_='unique')
        batch.drop_constraint('fk_cash_transfers_fee_charge_id', type_='foreignkey')
        batch.drop_column('fee_charge_id')
    op.drop_index('ix_account_transfers_to_account_id', table_name='account_transfers')
    op.drop_index('ix_account_transfers_account_id', table_name='account_transfers')
    op.drop_table('account_transfers')
