"""Money Facturation sends to the cash box, confirmed by the cashier

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c3e5a7b9d1f2'
down_revision = 'b2d4f6a8c0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'cash_transfers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('cash_accounts.id'), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=False),
        sa.Column('reference', sa.String(length=60), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('photo_key', sa.String(length=200), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='sent'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('received_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('received_at', sa.DateTime(), nullable=True),
        sa.Column('refused_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('refused_at', sa.DateTime(), nullable=True),
        sa.Column('refused_note', sa.String(length=255), nullable=True),
    )
    op.create_index('ix_cash_transfers_account_id', 'cash_transfers', ['account_id'])
    op.create_index('ix_cash_transfers_status', 'cash_transfers', ['status'])
    with op.batch_alter_table('cash_movements') as batch:
        batch.add_column(sa.Column('transfer_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_cash_movements_transfer_id', 'cash_transfers', ['transfer_id'], ['id'])
        batch.create_unique_constraint('uq_cash_movements_transfer_id', ['transfer_id'])


def downgrade():
    with op.batch_alter_table('cash_movements') as batch:
        batch.drop_constraint('uq_cash_movements_transfer_id', type_='unique')
        batch.drop_constraint('fk_cash_movements_transfer_id', type_='foreignkey')
        batch.drop_column('transfer_id')
    op.drop_index('ix_cash_transfers_status', table_name='cash_transfers')
    op.drop_index('ix_cash_transfers_account_id', table_name='cash_transfers')
    op.drop_table('cash_transfers')
