"""Link a citerne to its tanker vehicle

A citerne is also a truck that drives and consumes fuel. Linking it to a
Vehicle lets its own consumption (conso propre) be attributed to that vehicle
and its maintenance be tracked like any engin. Optional — a citerne may have
no vehicle yet.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2f3a4b5c6d7'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.add_column(sa.Column('vehicle_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_citernes_vehicle_id', 'vehicles', ['vehicle_id'], ['id'])


def downgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.drop_constraint('fk_citernes_vehicle_id', type_='foreignkey')
        batch_op.drop_column('vehicle_id')
