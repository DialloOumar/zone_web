"""Add category tracking method + daily-entry hour-meter index

VehicleCategory.tracking declares how a daily entry is logged for that
category ("trips" | "hours" | "hours_index"), chosen at creation. For
hours_index categories the operator reads the machine's hour-meter, so
DailyEntry gains index_start / index_end (hours = end - start).

Existing rows are backfilled: tracking mirrors the current unit_type.
Batch mode for SQLite + Postgres.

Revision ID: f5a7d2c9e1b3
Revises: d4e6c3a8b1f2
Create Date: 2026-06-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5a7d2c9e1b3'
down_revision = 'd4e6c3a8b1f2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicle_categories') as batch_op:
        batch_op.add_column(sa.Column('tracking', sa.String(length=20),
                                      nullable=False, server_default='trips'))
    # Backfill from the existing unit_type (trips stays the default).
    op.execute("UPDATE vehicle_categories SET tracking = 'hours' WHERE unit_type = 'hours'")

    with op.batch_alter_table('daily_entries') as batch_op:
        batch_op.add_column(sa.Column('index_start', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('index_end', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('daily_entries') as batch_op:
        batch_op.drop_column('index_end')
        batch_op.drop_column('index_start')
    with op.batch_alter_table('vehicle_categories') as batch_op:
        batch_op.drop_column('tracking')
