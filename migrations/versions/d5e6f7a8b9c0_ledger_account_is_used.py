"""The accounts the finance team uses, marked on the plan

The pickers on Caisse, Factures fournisseurs and Facturation list the
accounts marked used and nothing else. A company account starts used.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ledger_accounts') as batch_op:
        batch_op.add_column(sa.Column('is_used', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
    op.execute("UPDATE ledger_accounts SET is_used = TRUE WHERE is_standard = FALSE")


def downgrade():
    with op.batch_alter_table('ledger_accounts') as batch_op:
        batch_op.drop_column('is_used')
