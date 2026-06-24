"""Add maintenance_records.operator (driver linked to a service)

The conducteur associated with the entretien, stored as a name string like
DailyEntry.operator so history survives an operator rename/archive. Batch mode
for SQLite + Postgres.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maintenance_records') as batch_op:
        batch_op.add_column(sa.Column('operator', sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table('maintenance_records') as batch_op:
        batch_op.drop_column('operator')
