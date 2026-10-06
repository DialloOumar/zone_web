"""What clients owed and what was owed to suppliers when the app started

Revision ID: d4f6b8c0e2a3
Revises: c3e5a7b9d1f2
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4f6b8c0e2a3'
down_revision = 'c3e5a7b9d1f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('client_invoices') as batch:
        batch.add_column(sa.Column('is_opening', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('supplier_invoices') as batch:
        batch.add_column(sa.Column('is_opening', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('supplier_invoices') as batch:
        batch.drop_column('is_opening')
    with op.batch_alter_table('client_invoices') as batch:
        batch.drop_column('is_opening')
