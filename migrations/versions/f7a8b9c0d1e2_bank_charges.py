"""Charges paid straight from the bank

A charge paid by transfer or cheque with no supplier bill behind it,
recorded on the Banque page next to the bills.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a8b9c0d1e2'
down_revision = 'e6f7a8b9c0d1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bank_charges',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('cash_accounts.id'), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=5), nullable=False, server_default='GNF'),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('payee', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('ledger_code', sa.String(length=12), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_bank_charges_account_id', 'bank_charges', ['account_id'])
    op.create_index('ix_bank_charges_ledger_code', 'bank_charges', ['ledger_code'])


def downgrade():
    op.drop_index('ix_bank_charges_ledger_code', table_name='bank_charges')
    op.drop_index('ix_bank_charges_account_id', table_name='bank_charges')
    op.drop_table('bank_charges')
