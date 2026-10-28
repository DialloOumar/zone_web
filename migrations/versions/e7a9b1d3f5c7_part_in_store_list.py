"""A part can be in a store's list with no re-order level

A store taking up a part another store already has puts it in its own list,
level or not: the re-order level becomes optional on that row.

Revision ID: e7a9b1d3f5c7
Revises: d6f8a0c2e4b6
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7a9b1d3f5c7'
down_revision = 'd6f8a0c2e4b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('part_store_levels') as b:
        b.alter_column('reorder_level', existing_type=sa.Float(), nullable=True)


def downgrade():
    op.execute("DELETE FROM part_store_levels WHERE reorder_level IS NULL")
    with op.batch_alter_table('part_store_levels') as b:
        b.alter_column('reorder_level', existing_type=sa.Float(), nullable=False)
