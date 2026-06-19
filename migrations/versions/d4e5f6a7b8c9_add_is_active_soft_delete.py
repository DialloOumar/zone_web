"""Add is_active (soft delete) to fleets, vehicle_categories, roles

Soft delete = archive: deleting a reference record now flips is_active to
False instead of removing it, so history and links are preserved and the
record can be reactivated. Operators, vehicles and users already had
is_active. Batch mode for SQLite + Postgres.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('fleets', 'vehicle_categories', 'roles'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column(
                'is_active', sa.Boolean(), nullable=False, server_default=sa.true()
            ))


def downgrade():
    for table in ('roles', 'vehicle_categories', 'fleets'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('is_active')
