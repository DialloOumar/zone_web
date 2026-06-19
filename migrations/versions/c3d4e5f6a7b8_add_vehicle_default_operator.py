"""Add vehicles.default_operator_id (optional default driver)

A vehicle can have an optional default driver — a real FK to a registered
Operator (same fleet) — used to pre-fill the conducteur on a new daily entry.
Batch mode for SQLite + Postgres.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.add_column(sa.Column('default_operator_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_vehicles_default_operator', 'operators', ['default_operator_id'], ['id']
        )


def downgrade():
    with op.batch_alter_table('vehicles') as batch_op:
        batch_op.drop_constraint('fk_vehicles_default_operator', type_='foreignkey')
        batch_op.drop_column('default_operator_id')
