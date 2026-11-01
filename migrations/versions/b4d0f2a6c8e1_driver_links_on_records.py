"""The driver on a record is a link to the person, not only a name

Daily entries, services, fuel movements and costs wrote the driver as the
words chosen in the dropdown. They now also link the person's file
(driver_id); the words stay in the "operator" column as a trace of what was
chosen then.

Past records are linked where the name is the driver's, written the same way
(case and spacing aside): first among the people who drive for the record's
fleet, else among everybody when only one person bears it. What matches no
file stays a name, shown as before; the merge screen lists those names so
they can be tied to a person by hand, or left as they are
(driver_names_ignored).

Revision ID: b4d0f2a6c8e1
Revises: a3c9e1f5b7d2
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'b4d0f2a6c8e1'
down_revision = 'a3c9e1f5b7d2'
branch_labels = None
depends_on = None

TABLES = ('daily_entries', 'maintenance_records', 'fuel_movements', 'expenses')

# Where each table's fleet comes from, to look the name up among that fleet's
# drivers first.
FLEET_OF = {
    'daily_entries': ("LEFT JOIN vehicles v ON v.id = t.vehicle_id", "v.fleet_id"),
    'maintenance_records': ("LEFT JOIN vehicles v ON v.id = t.vehicle_id", "v.fleet_id"),
    'fuel_movements': ("LEFT JOIN vehicles v ON v.id = t.vehicle_id "
                       "LEFT JOIN citernes c ON c.id = t.citerne_id",
                       "COALESCE(v.fleet_id, c.fleet_id)"),
    'expenses': ("LEFT JOIN vehicles v ON v.id = t.vehicle_id",
                 "COALESCE(t.fleet_id, v.fleet_id)"),
}


def _key(text):
    return " ".join((text or "").lower().split())


def upgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as b:
            b.add_column(sa.Column('driver_id', sa.Integer()))
            b.create_foreign_key('fk_%s_driver' % table, 'staff', ['driver_id'], ['id'])
        op.create_index('ix_%s_driver_id' % table, table, ['driver_id'])

    op.create_table(
        'driver_names_ignored',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False, unique=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    conn = op.get_bind()
    names = {}
    for sid, last, first in conn.execute(sa.text(
            "SELECT id, last_name, first_name FROM staff")).fetchall():
        names[sid] = " ".join(p for p in (last, first) if p)
    by_fleet = {}
    for fid, sid in conn.execute(sa.text("SELECT fleet_id, staff_id FROM operators")).fetchall():
        by_fleet.setdefault((fid, _key(names.get(sid))), set()).add(sid)
    everybody = {}
    for sid, n in names.items():
        everybody.setdefault(_key(n), set()).add(sid)

    for table in TABLES:
        join, fleet = FLEET_OF[table]
        rows = conn.execute(sa.text(
            "SELECT t.id, %s, t.operator FROM %s t %s "
            "WHERE t.operator IS NOT NULL AND t.driver_id IS NULL" % (fleet, table, join))).fetchall()
        groups = {}
        for rid, fid, name in rows:
            k = _key(name)
            match = by_fleet.get((fid, k)) or everybody.get(k) or set()
            if len(match) == 1:
                groups.setdefault(next(iter(match)), []).append(rid)
        for sid, ids in groups.items():
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                conn.execute(sa.text(
                    "UPDATE %s SET driver_id = :s WHERE id IN (%s)"
                    % (table, ",".join(str(int(x)) for x in chunk))), {"s": sid})


def downgrade():
    op.drop_table('driver_names_ignored')
    for table in TABLES:
        op.drop_index('ix_%s_driver_id' % table, table)
        with op.batch_alter_table(table) as b:
            b.drop_constraint('fk_%s_driver' % table, type_='foreignkey')
            b.drop_column('driver_id')
