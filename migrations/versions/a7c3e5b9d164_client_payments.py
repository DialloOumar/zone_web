"""Payments received against client invoices

One row per payment a client made on a bill; part payments add up and the
bill's state (unpaid, partial, paid, overdue) is read off them.

Revision ID: a7c3e5b9d164
Revises: f6b2d9e4a853
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c3e5b9d164'
down_revision = 'f6b2d9e4a853'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'client_payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=True),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['invoice_id'], ['client_invoices.id']),
        sa.ForeignKeyConstraint(['account_id'], ['cash_accounts.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_payments_invoice_id', 'client_payments', ['invoice_id'])


def downgrade():
    op.drop_index('ix_client_payments_invoice_id', table_name='client_payments')
    op.drop_table('client_payments')
