"""Conversions between units, kept apart from the units

A unit is a unit; what one makes of another ("1 fût = 200 litre") is a
conversion of its own, one row per pair. The conversions carried on the
units move there.

Revision ID: e2c4a6b8d0f1
Revises: c0d1e2f3a4b5
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2c4a6b8d0f1'
down_revision = 'c0d1e2f3a4b5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'unit_conversions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('from_code', sa.String(length=20), nullable=False),
        sa.Column('to_code', sa.String(length=20), nullable=False),
        sa.Column('factor', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('from_code', 'to_code', name='uq_unit_conversion_pair'),
    )
    op.create_index('ix_unit_conversions_from_code', 'unit_conversions', ['from_code'])
    op.create_index('ix_unit_conversions_to_code', 'unit_conversions', ['to_code'])
    op.execute("""
        INSERT INTO unit_conversions (from_code, to_code, factor, created_at)
        SELECT code, base_code, factor, CURRENT_TIMESTAMP FROM part_units
         WHERE base_code IS NOT NULL AND factor IS NOT NULL
    """)
    with op.batch_alter_table('part_units') as batch_op:
        batch_op.drop_index('ix_part_units_base_code')
        batch_op.drop_column('factor')
        batch_op.drop_column('base_code')


def downgrade():
    with op.batch_alter_table('part_units') as batch_op:
        batch_op.add_column(sa.Column('base_code', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('factor', sa.Float(), nullable=True))
        batch_op.create_index('ix_part_units_base_code', ['base_code'])
    op.execute("""
        UPDATE part_units SET
          base_code = (SELECT to_code FROM unit_conversions c WHERE c.from_code = part_units.code LIMIT 1),
          factor = (SELECT factor FROM unit_conversions c WHERE c.from_code = part_units.code LIMIT 1)
    """)
    op.drop_index('ix_unit_conversions_to_code', table_name='unit_conversions')
    op.drop_index('ix_unit_conversions_from_code', table_name='unit_conversions')
    op.drop_table('unit_conversions')
