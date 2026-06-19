"""Drop operator_morning / operator_evening from vehicles

The morning/evening driver fields on the vehicle are removed — the driver
(conducteur) is now chosen per daily entry from the registered operators.
Batch mode for SQLite + Postgres.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_column('operator_morning')
        batch_op.drop_column('operator_evening')


def downgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('operator_evening', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('operator_morning', sa.String(length=120), nullable=True))
