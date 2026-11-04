"""Advances on salary, handed out by the cash box

A third kind of cash movement, "avance": money out of the box to an employee,
lent and not spent, taken back in full from the salary of a given month.

Revision ID: c2e8a4b0d6f7
Revises: b1d7f3a9c5e6
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'c2e8a4b0d6f7'
down_revision = 'b1d7f3a9c5e6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('cash_movements') as batch:
        batch.add_column(sa.Column('staff_id', sa.Integer()))
        batch.add_column(sa.Column('advance_month', sa.String(7)))
        batch.create_foreign_key('fk_cash_movements_staff_id', 'staff', ['staff_id'], ['id'])
        batch.create_index('ix_cash_movements_staff_id', ['staff_id'])


def downgrade():
    # The advances go with their columns: they would read as nothing else.
    op.execute("DELETE FROM cash_movements WHERE kind = 'avance'")
    with op.batch_alter_table('cash_movements') as batch:
        batch.drop_index('ix_cash_movements_staff_id')
        batch.drop_constraint('fk_cash_movements_staff_id', type_='foreignkey')
        batch.drop_column('advance_month')
        batch.drop_column('staff_id')
