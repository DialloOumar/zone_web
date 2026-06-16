"""Drop fuel_liters from daily_entries

Fuel is no longer logged on a daily entry — it is captured as an Expense
(category 'fuel'). Uses batch mode so the column drop also works on SQLite.

Revision ID: b2f4a1c6d3e7
Revises: 1da4b139d7c2
Create Date: 2026-06-16 21:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2f4a1c6d3e7'
down_revision = '1da4b139d7c2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('daily_entries') as batch_op:
        batch_op.drop_column('fuel_liters')


def downgrade():
    with op.batch_alter_table('daily_entries') as batch_op:
        batch_op.add_column(sa.Column('fuel_liters', sa.Float(), nullable=True))
