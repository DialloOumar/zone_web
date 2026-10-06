"""Several supplier bills settled by one transfer

Revision ID: f6b8d0e2a4c5
Revises: e5a7c9d1f3b4
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f6b8d0e2a4c5'
down_revision = 'e5a7c9d1f3b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supplier_payments') as batch:
        batch.add_column(sa.Column('batch', sa.String(length=32), nullable=True))
    op.create_index('ix_supplier_payments_batch', 'supplier_payments', ['batch'])


def downgrade():
    op.drop_index('ix_supplier_payments_batch', table_name='supplier_payments')
    with op.batch_alter_table('supplier_payments') as batch:
        batch.drop_column('batch')
