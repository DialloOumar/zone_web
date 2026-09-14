"""A default drawing a vehicle category can carry

Optional, and empty for every category that already exists: which drawing
suits which category is for whoever manages them to choose, not for a
migration to guess.

Revision ID: f2c8d5e1b307
Revises: e1b7c4d2a805
Create Date: 2026-09-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f2c8d5e1b307'
down_revision = 'e1b7c4d2a805'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicle_categories') as batch_op:
        batch_op.add_column(sa.Column('default_image', sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table('vehicle_categories') as batch_op:
        batch_op.drop_column('default_image')
