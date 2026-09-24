"""The ink colour of a user's signature strokes, apart from the stamp's

Revision ID: e8a0c3d6f975
Revises: d7f9b2c5e864
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e8a0c3d6f975'
down_revision = 'd7f9b2c5e864'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('sig_color', sa.String(length=7), nullable=True))


def downgrade():
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('sig_color')
