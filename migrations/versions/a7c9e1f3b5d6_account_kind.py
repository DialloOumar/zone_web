"""A company account is a bank or a mobile-money line

Revision ID: a7c9e1f3b5d6
Revises: f6b8d0e2a4c5
Create Date: 2026-10-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7c9e1f3b5d6'
down_revision = 'f6b8d0e2a4c5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cash_accounts') as batch:
        batch.add_column(sa.Column('kind', sa.String(length=10), nullable=False, server_default='bank'))


def downgrade():
    with op.batch_alter_table('cash_accounts') as batch:
        batch.drop_column('kind')
