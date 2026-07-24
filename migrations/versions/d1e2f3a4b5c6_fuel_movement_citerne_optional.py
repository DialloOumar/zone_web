"""Make a fuel movement's citerne optional (direct fills at the client)

Buses normally fill directly at the client, with no citerne in between. Such a
"direct" fuel movement has no source citerne, so citerne_id becomes nullable.
Rentrées, distributions, relevés and conso still carry a citerne; only the new
"direct" kind leaves it null.

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c9d0e1f2a3b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('fuel_movements') as batch_op:
        batch_op.alter_column('citerne_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    # Only valid if no direct fills exist (they have a null citerne_id).
    with op.batch_alter_table('fuel_movements') as batch_op:
        batch_op.alter_column('citerne_id', existing_type=sa.Integer(), nullable=False)
