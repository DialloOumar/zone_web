"""Add service_type to maintenance_rules

A rule now declares which service it schedules (oil_change, filter, …) so the
log-service form can pre-fill the type and the engine can match services by
type. Batch mode for SQLite + Postgres.

Revision ID: d4e6c3a8b1f2
Revises: c3d5b2e7f8a1
Create Date: 2026-06-16 22:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e6c3a8b1f2'
down_revision = 'c3d5b2e7f8a1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('maintenance_rules') as batch_op:
        batch_op.add_column(sa.Column('service_type', sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table('maintenance_rules') as batch_op:
        batch_op.drop_column('service_type')
