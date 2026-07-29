"""Drop the citerne → tanker vehicle link

The optional link from a citerne to the vehicle carrying it was removed from
the feature: a citerne is just a reservoir, its own consumption stays on the
citerne. Batch mode so the column drop also works on SQLite.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7e8f9a0b1c2'
down_revision = 'c6d7e8f9a0b1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.drop_constraint('fk_citernes_vehicle_id', type_='foreignkey')
        batch_op.drop_column('vehicle_id')


def downgrade():
    with op.batch_alter_table('citernes') as batch_op:
        batch_op.add_column(sa.Column('vehicle_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_citernes_vehicle_id', 'vehicles', ['vehicle_id'], ['id'])
