"""Clear the seeded reference consumptions

The baseline was typed in by hand (per category, optionally overridden per
vehicle) and shipped with seeded guesses — 12 L/trip for a bus, 25 L/h for a
TSF truck. Those inputs are gone: the reference is to be derived from the
fleet's own logged activity after a few months of operations.

Left behind, the seeded numbers would keep driving "Fuel vs baseline" with
invented figures nobody could correct, so they are blanked here. The columns
stay — that is where the derived value will land.

No downgrade: the values were placeholders, and restoring guesses would
recreate the problem.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-31 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e8f9a0b1c2d3'
down_revision = 'd7e8f9a0b1c2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE vehicle_categories SET default_baseline_l_per_unit = NULL")
    op.execute("UPDATE vehicles SET baseline_l_per_unit_override = NULL")


def downgrade():
    # Nothing to restore: what was cleared was placeholder data.
    pass
