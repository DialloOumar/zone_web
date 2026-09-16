"""Plan comptable: the ledger accounts table

One row per account of the SYSCOHADA révisé chart, seeded from the data file
on boot, plus the sub-accounts the company adds under them. The first screen
of the Finance workspace.

Revision ID: a1f3c7e9b205
Revises: d2b8f5a1c794
Create Date: 2026-09-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a1f3c7e9b205'
down_revision = 'd2b8f5a1c794'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'ledger_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=12), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('klass', sa.Integer(), nullable=False),
        sa.Column('parent_code', sa.String(length=12), nullable=True),
        sa.Column('is_standard', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_ledger_accounts_code'),
    )
    op.create_index('ix_ledger_accounts_parent_code', 'ledger_accounts', ['parent_code'])


def downgrade():
    op.drop_index('ix_ledger_accounts_parent_code', table_name='ledger_accounts')
    op.drop_table('ledger_accounts')
