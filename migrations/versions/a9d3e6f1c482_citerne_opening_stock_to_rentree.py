"""A citerne no longer has an opening stock: it starts empty, and fuel comes in
as rentrées.

Every existing opening stock becomes a rentrée noted "Stock d'ouverture", so no
litre disappears and it shows in the movement list like any other.

The opening stock used to count before everything else, whatever its date (it
carries the day it was typed, often after the first draws). A rentrée counts on
its date, so it is moved no later than the citerne's first rentrée,
distribution or relevé — and a day earlier if a movement on that first day was
entered before it, since movements on the same day go in the order they were
entered. Every level, and every relevé's écart, therefore stays what it was.

Revision ID: a9d3e6f1c482
Revises: f2c8d5e1b307
Create Date: 2026-09-14
"""
from datetime import date, timedelta

import sqlalchemy as sa
from alembic import op

revision = 'a9d3e6f1c482'
down_revision = 'f2c8d5e1b307'
branch_labels = None
depends_on = None

NOTE = "Stock d'ouverture"


def upgrade():
    bind = op.get_bind()
    openings = bind.execute(sa.text(
        "SELECT id, citerne_id, date FROM fuel_movements "
        "WHERE kind = 'initial' AND citerne_id IS NOT NULL")).fetchall()
    for mid, cid, day in openings:
        first = bind.execute(sa.text(
            "SELECT date, id FROM fuel_movements "
            "WHERE citerne_id = :cid AND id != :mid "
            "AND kind IN ('rentree', 'distribution', 'releve') "
            "ORDER BY date, id LIMIT 1"), {"cid": cid, "mid": mid}).fetchone()
        new_day = day
        if first is not None and first[0] <= day:
            first_day, first_id = first
            if first_id < mid:
                new_day = (date.fromisoformat(first_day) - timedelta(days=1)).isoformat()
            else:
                new_day = first_day
        bind.execute(sa.text(
            "UPDATE fuel_movements SET kind = 'rentree', date = :day, "
            "note = COALESCE(note, :note) WHERE id = :mid"),
            {"day": new_day, "note": NOTE, "mid": mid})


def downgrade():
    # Best effort: the rows turn back into opening stocks. Their original date
    # is not kept, and an opening stock counts first whatever its date anyway.
    op.get_bind().execute(sa.text(
        "UPDATE fuel_movements SET kind = 'initial' "
        "WHERE kind = 'rentree' AND note = :note"), {"note": NOTE})
