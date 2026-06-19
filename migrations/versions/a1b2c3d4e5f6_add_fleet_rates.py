"""Add fleet_rates (dated per-client billing rate by category)

Each fleet (client) bills a rate in GNF per worked unit (trip / hour) for each
machine type. Rates are dated history — changing a price inserts a new row, so
past periods re-bill at the price that applied then. The rate in force on a
date = the row with the greatest effective_from <= that date.

Revision ID: a1b2c3d4e5f6
Revises: f5a7d2c9e1b3
Create Date: 2026-06-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'f5a7d2c9e1b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'fleet_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fleet_id', sa.Integer(), nullable=False),
        sa.Column('category_code', sa.String(length=30), nullable=False),
        sa.Column('rate_per_unit', sa.Integer(), nullable=False),
        sa.Column('effective_from', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['fleet_id'], ['fleets.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('fleet_rates') as batch_op:
        batch_op.create_index('ix_fleet_rates_lookup', ['fleet_id', 'category_code', 'effective_from'])


def downgrade():
    with op.batch_alter_table('fleet_rates') as batch_op:
        batch_op.drop_index('ix_fleet_rates_lookup')
    op.drop_table('fleet_rates')
