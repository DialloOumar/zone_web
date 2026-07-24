"""Add citernes and fuel movements

Introduces ZONE's fuel tankers (citernes) and their dated movement log. Stock
is computed from the movements, not stored. This is the first slice of the
fuel rework: create/activate citernes, and log a distribution (an engin drawing
fuel from a citerne). Rentrées / conso / relevés reuse the same movement table
later, so no further migration is needed for them.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'citernes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('capacity_liters', sa.Integer(), nullable=False),
        sa.Column('fleet_id', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['fleet_id'], ['fleets.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_citernes_code'),
    )
    op.create_table(
        'fuel_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('citerne_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('liters', sa.Integer(), nullable=False),
        sa.Column('vehicle_id', sa.Integer(), nullable=True),
        sa.Column('operator', sa.String(length=120), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['citerne_id'], ['citernes.id']),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_fuel_movements_citerne_id', 'fuel_movements', ['citerne_id'])


def downgrade():
    op.drop_index('ix_fuel_movements_citerne_id', table_name='fuel_movements')
    op.drop_table('fuel_movements')
    op.drop_table('citernes')
