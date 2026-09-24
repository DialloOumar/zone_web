"""The ink colour of a user's stamp

Revision ID: d7f9b2c5e864
Revises: c6e8a1b4d753
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd7f9b2c5e864'
down_revision = 'c6e8a1b4d753'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('stamp_color', sa.String(length=7), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('stamp_color')
