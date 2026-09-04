"""Quantity on an expense

Some cash-box costs are for parts, and the voucher records how many. This is
that number and nothing else: it moves no stock and joins to no article — the
parts store keeps its own count.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-09-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c2d3e4f5a6b7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('quantity', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_column('quantity')
