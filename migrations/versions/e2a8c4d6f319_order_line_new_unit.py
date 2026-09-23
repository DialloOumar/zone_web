"""A new-article order line carries the unit it will be counted in

Revision ID: e2a8c4d6f319
Revises: d1f7b9c5e208
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2a8c4d6f319'
down_revision = 'd1f7b9c5e208'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.add_column(sa.Column('new_unit', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.drop_column('new_unit')
