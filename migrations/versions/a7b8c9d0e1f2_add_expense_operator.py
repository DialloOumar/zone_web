"""Add expenses.operator (optional driver a cost is attributed to)

Lets a cost (e.g. fuel taken by a given driver) be tied to a conducteur,
stored as a name string like DailyEntry.operator so history survives an
operator rename/archive. Batch mode for SQLite + Postgres.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.add_column(sa.Column('operator', sa.String(length=120), nullable=True))


def downgrade():
    with op.batch_alter_table('expenses') as batch_op:
        batch_op.drop_column('operator')
