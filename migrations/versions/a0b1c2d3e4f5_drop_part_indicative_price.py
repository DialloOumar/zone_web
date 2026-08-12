"""Drop the part's indicative price

Two prices on screen meant neither was trusted: a catalogue price on the part
that matched no real parts, and the price actually paid on a receipt. The
catalogue one is gone. Every price now belongs to parts that exist — what the
opening stock was declared to be worth, and what each receipt cost — and the
next receipt pre-fills from the last price paid.

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4
Create Date: 2026-08-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a0b1c2d3e4f5'
down_revision = 'f9a0b1c2d3e4'
branch_labels = None
depends_on = None


def upgrade():
    # Anything already carrying the catalogue price keeps a real one: push it
    # onto the opening movement of parts whose opening stock has no price yet,
    # so no existing stock silently loses its value.
    op.execute("""
        UPDATE stock_movements
           SET unit_price = (SELECT p.unit_price FROM parts p
                              WHERE p.id = stock_movements.part_id)
         WHERE kind = 'initial'
           AND unit_price IS NULL
    """)
    with op.batch_alter_table('parts') as batch_op:
        batch_op.drop_column('unit_price')


def downgrade():
    with op.batch_alter_table('parts') as batch_op:
        batch_op.add_column(sa.Column('unit_price', sa.Integer(), nullable=True))
    # Best effort the other way: seed it from the opening movement's price.
    op.execute("""
        UPDATE parts
           SET unit_price = (SELECT m.unit_price FROM stock_movements m
                              WHERE m.part_id = parts.id AND m.kind = 'initial'
                              LIMIT 1)
    """)
