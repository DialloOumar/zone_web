"""Add time (HH:MM) to fuel_movements — the hour a fuel draw happened

A distribution / direct fill ("prise d'essence") can now record the time, not
just the date. Optional; existing rows stay NULL. Batch mode for SQLite + PG.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b5c6d7e8f9a0'
down_revision = 'a4b5c6d7e8f9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fuel_movements') as batch_op:
        batch_op.add_column(sa.Column('time', sa.String(length=5), nullable=True))


def downgrade():
    with op.batch_alter_table('fuel_movements') as batch_op:
        batch_op.drop_column('time')
