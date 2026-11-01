"""A vehicle's site is chosen from the list of sites, not typed

The site was a line of free text on the vehicle, while personnel, the cash
box and the stores already chose theirs from the sites list. Every text
already written is matched to the site of that name (case and spacing
aside), or added to the list, so nothing is lost; then the vehicle points at
it and the text column goes.

Revision ID: c5e1a7b3d9f2
Revises: b4d0f2a6c8e1
Create Date: 2026-11-01
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'c5e1a7b3d9f2'
down_revision = 'b4d0f2a6c8e1'
branch_labels = None
depends_on = None


def _key(text):
    return " ".join((text or "").lower().split())


def upgrade():
    with op.batch_alter_table('vehicles') as b:
        b.add_column(sa.Column('site_id', sa.Integer()))
        b.create_foreign_key('fk_vehicles_site', 'sites', ['site_id'], ['id'])

    conn = op.get_bind()
    sites = {_key(n): sid for sid, n in conn.execute(sa.text("SELECT id, name FROM sites")).fetchall()}
    for (text,) in conn.execute(sa.text(
            "SELECT DISTINCT site FROM vehicles WHERE site IS NOT NULL")).fetchall():
        k = _key(text)
        if not k:
            continue
        sid = sites.get(k)
        if sid is None:
            nxt = conn.execute(sa.text("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM sites")).scalar()
            name = " ".join(text.split())[:80]
            conn.execute(sa.text(
                "INSERT INTO sites (name, sort_order, is_active, created_at) VALUES (:n, :s, :a, :c)"),
                {"n": name, "s": nxt, "a": True, "c": datetime.utcnow()})
            sid = conn.execute(sa.text("SELECT id FROM sites WHERE name = :n"), {"n": name}).scalar()
            sites[k] = sid
        conn.execute(sa.text("UPDATE vehicles SET site_id = :s WHERE site = :t"), {"s": sid, "t": text})

    with op.batch_alter_table('vehicles') as b:
        b.drop_column('site')


def downgrade():
    with op.batch_alter_table('vehicles') as b:
        b.add_column(sa.Column('site', sa.String(80)))
    op.execute(sa.text(
        "UPDATE vehicles SET site = (SELECT name FROM sites WHERE sites.id = vehicles.site_id)"))
    with op.batch_alter_table('vehicles') as b:
        b.drop_constraint('fk_vehicles_site', type_='foreignkey')
        b.drop_column('site_id')
