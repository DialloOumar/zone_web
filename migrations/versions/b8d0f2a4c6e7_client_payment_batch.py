"""Several client bills settled by one transfer

Revision ID: b8d0f2a4c6e7
Revises: a7c9e1f3b5d6
Create Date: 2026-10-07 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8d0f2a4c6e7'
down_revision = 'a7c9e1f3b5d6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('client_payments') as batch:
        batch.add_column(sa.Column('batch', sa.String(length=32), nullable=True))
    op.create_index('ix_client_payments_batch', 'client_payments', ['batch'])


def downgrade():
    op.drop_index('ix_client_payments_batch', table_name='client_payments')
    with op.batch_alter_table('client_payments') as batch:
        batch.drop_column('batch')
