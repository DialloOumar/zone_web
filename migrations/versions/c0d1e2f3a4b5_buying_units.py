"""Buying units, defined once, converting to a counting unit

A unit may now be one things are bought in -- a fût of 200 litres -- that
converts to one the store counts in; it is defined once in the units list
and offered on every order line whose part counts in what it converts to.
A part no longer carries a pack of its own: its pack, where it had one,
becomes such a unit, and its order lines bought per pack point at it.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-09-25 00:00:00.000000

"""
import re
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = 'c0d1e2f3a4b5'
down_revision = 'b9c0d1e2f3a4'
branch_labels = None
depends_on = None


def _slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:20]


def upgrade():
    with op.batch_alter_table('part_units') as batch_op:
        batch_op.add_column(sa.Column('base_code', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('factor', sa.Float(), nullable=True))
        batch_op.create_index('ix_part_units_base_code', ['base_code'])
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.add_column(sa.Column('buy_unit', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('factor', sa.Float(), nullable=False, server_default='1'))

    # Every pack a part had becomes a buying unit, one per (name, size,
    # unit), and the lines bought per pack point at it with its factor.
    conn = op.get_bind()
    parts = conn.execute(sa.text(
        "SELECT id, unit, pack_name, pack_size FROM parts "
        "WHERE pack_name IS NOT NULL AND pack_size IS NOT NULL AND pack_size > 0")).fetchall()
    existing = {r[0] for r in conn.execute(sa.text("SELECT code FROM part_units")).fetchall()}
    order = conn.execute(sa.text("SELECT COALESCE(MAX(sort_order), 0) FROM part_units")).scalar() or 0
    made = {}
    for pid, unit, name, size in parts:
        key = (name.strip().lower(), float(size), unit)
        if key not in made:
            code = _slug("%s_%g_%s" % (name, size, unit))
            n = 1
            while code in existing:
                n += 1
                code = (_slug("%s_%g_%s" % (name, size, unit))[:17] + "_%d" % n)
            unit_name = "%s (%g %s)" % (name.strip(), size, unit)
            order += 1
            conn.execute(sa.text(
                "INSERT INTO part_units (code, name, base_code, factor, sort_order, is_active, created_at) "
                "VALUES (:c, :n, :b, :f, :o, TRUE, CURRENT_TIMESTAMP)"),
                dict(c=code, n=unit_name[:40], b=unit, f=float(size), o=order))
            existing.add(code)
            made[key] = code
        conn.execute(sa.text(
            "UPDATE purchase_order_lines SET buy_unit = :c, factor = :f "
            "WHERE part_id = :p AND in_pack = TRUE"), dict(c=made[key], f=float(size), p=pid))

    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.drop_column('in_pack')
    with op.batch_alter_table('parts') as batch_op:
        batch_op.drop_column('pack_name')
        batch_op.drop_column('pack_size')


def downgrade():
    with op.batch_alter_table('parts') as batch_op:
        batch_op.add_column(sa.Column('pack_name', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('pack_size', sa.Float(), nullable=True))
    with op.batch_alter_table('purchase_order_lines') as batch_op:
        batch_op.add_column(sa.Column('in_pack', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.drop_column('factor')
        batch_op.drop_column('buy_unit')
    with op.batch_alter_table('part_units') as batch_op:
        batch_op.drop_index('ix_part_units_base_code')
        batch_op.drop_column('factor')
        batch_op.drop_column('base_code')
