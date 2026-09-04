"""An optional account number on a cash account.

Free text and nullable: an account here may be a bank account, a mobile money
line, or nothing at all. It is written down so the cashier can quote it, never
matched or added up.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""
import sqlalchemy as sa
from alembic import op

revision = 'e4f5a6b7c8d9'
down_revision = 'd3e4f5a6b7c8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cash_accounts') as b:
        b.add_column(sa.Column('number', sa.String(length=60), nullable=True))


def downgrade():
    with op.batch_alter_table('cash_accounts') as b:
        b.drop_column('number')
