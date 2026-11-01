"""One person table: drivers and logins point to the person's file

A driver on the payroll with a login existed three times -- a user, a staff
row, an operator per fleet -- each with its own copy of the name and phone.
Now the staff row is the person, and:

- an operator row is only "this person drives for this fleet": its name,
  phone, licence and notes move onto the person and the columns go;
- a login may point to the employee it belongs to (users.staff_id).

The staff file also gains what an HR file holds (kind employee / external,
identity, contact, CNSS, licence, photo) and a table of scanned documents.

Existing rows are joined up here only where the name is the same, written the
same way (case and spacing aside). Every operator whose name matches no
person becomes a person of its own -- an employee, as driver, with the name
whole in the surname, as the staff split did. Near-matches ("BAH Mamadou" /
"Mamadou Bah") are left to the merge screen, where somebody who knows decides.
A login is tied to a person only when exactly one person bears its name.

Revision ID: a3c9e1f5b7d2
Revises: f8b0c2d4e6a8
Create Date: 2026-10-30
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = 'a3c9e1f5b7d2'
down_revision = 'f8b0c2d4e6a8'
branch_labels = None
depends_on = None

DRIVER_POSITION = "Chauffeur"


def _key(text):
    return " ".join((text or "").lower().split())


def _display(last, first):
    return " ".join(p for p in (last, first) if p)


def rename_on_records(conn, fleet_id, old, new):
    """Rewrite a driver's name on what one fleet recorded under it."""
    in_fleet = "vehicle_id IN (SELECT id FROM vehicles WHERE fleet_id = :f)"
    p = {"f": fleet_id, "o": old, "n": new}
    conn.execute(sa.text("UPDATE daily_entries SET operator = :n WHERE operator = :o AND " + in_fleet), p)
    conn.execute(sa.text("UPDATE maintenance_records SET operator = :n WHERE operator = :o AND " + in_fleet), p)
    conn.execute(sa.text("UPDATE fuel_movements SET operator = :n WHERE operator = :o AND "
                         "(vehicle_id IS NULL OR " + in_fleet + ")"), p)
    conn.execute(sa.text("UPDATE expenses SET operator = :n WHERE operator = :o AND "
                         "(fleet_id IS NULL OR fleet_id = :f)"), p)


def upgrade():
    with op.batch_alter_table('staff') as b:
        b.add_column(sa.Column('kind', sa.String(10), nullable=False, server_default='employee'))
        b.add_column(sa.Column('sex', sa.String(1)))
        b.add_column(sa.Column('birth_date', sa.String(10)))
        b.add_column(sa.Column('birth_place', sa.String(80)))
        b.add_column(sa.Column('nationality', sa.String(60)))
        b.add_column(sa.Column('id_number', sa.String(60)))
        b.add_column(sa.Column('photo_key', sa.String(200)))
        b.add_column(sa.Column('address', sa.String(200)))
        b.add_column(sa.Column('emergency_name', sa.String(120)))
        b.add_column(sa.Column('emergency_phone', sa.String(30)))
        b.add_column(sa.Column('site_id', sa.Integer()))
        b.add_column(sa.Column('cnss_number', sa.String(40)))
        b.add_column(sa.Column('company', sa.String(120)))
        b.add_column(sa.Column('license_number', sa.String(40)))
        b.add_column(sa.Column('license_categories', sa.String(40)))
        b.add_column(sa.Column('license_expires', sa.String(10)))
        b.create_foreign_key('fk_staff_site', 'sites', ['site_id'], ['id'])

    op.create_table(
        'staff_documents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('staff.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('file_key', sa.String(200), nullable=False),
        sa.Column('content_type', sa.String(60)),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_staff_documents_staff_id', 'staff_documents', ['staff_id'])

    op.create_table(
        'person_match_dismissed',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('staff_a_id', sa.Integer(), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
        sa.Column('staff_b_id', sa.Integer(), sa.ForeignKey('staff.id', ondelete='CASCADE')),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE')),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )

    with op.batch_alter_table('users') as b:
        b.add_column(sa.Column('staff_id', sa.Integer()))
        b.create_foreign_key('fk_users_staff', 'staff', ['staff_id'], ['id'])
        b.create_unique_constraint('uq_users_staff', ['staff_id'])

    with op.batch_alter_table('operators') as b:
        b.add_column(sa.Column('staff_id', sa.Integer()))

    conn = op.get_bind()
    now = datetime.utcnow()

    # The driver's position, created if the list does not have it yet.
    pos_id = conn.execute(sa.text(
        "SELECT id FROM staff_positions WHERE lower(name) = lower(:n)"),
        {"n": DRIVER_POSITION}).scalar()
    if pos_id is None:
        nxt = conn.execute(sa.text(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM staff_positions")).scalar()
        conn.execute(sa.text(
            "INSERT INTO staff_positions (name, sort_order, is_active, created_at) "
            "VALUES (:n, :s, :a, :c)"),
            {"n": DRIVER_POSITION, "s": nxt, "a": True, "c": now})
        pos_id = conn.execute(sa.text(
            "SELECT id FROM staff_positions WHERE name = :n"), {"n": DRIVER_POSITION}).scalar()

    people = {}       # name key -> staff id (first one wins when two share it)
    for sid, last, first in conn.execute(sa.text(
            "SELECT id, last_name, first_name FROM staff ORDER BY id")).fetchall():
        people.setdefault(_key(_display(last, first)), sid)

    ops = conn.execute(sa.text(
        "SELECT id, name, phone, license_number, notes, is_active, created_by, created_at "
        "FROM operators ORDER BY id")).fetchall()
    for oid, name, phone, lic, notes, active, cby, cat in ops:
        key = _key(name)
        sid = people.get(key)
        if sid is None:
            conn.execute(sa.text(
                "INSERT INTO staff (last_name, first_name, position_id, phone, note, "
                "is_active, kind, license_number, created_by, created_at) "
                "VALUES (:ln, NULL, :p, :ph, :nt, :a, 'employee', :lic, :cb, :ca)"),
                {"ln": " ".join(name.split())[:80], "p": pos_id, "ph": phone,
                 "nt": notes, "a": bool(active), "lic": lic, "cb": cby, "ca": cat or now})
            sid = conn.execute(sa.text(
                "SELECT MAX(id) FROM staff WHERE last_name = :ln AND first_name IS NULL"),
                {"ln": " ".join(name.split())[:80]}).scalar()
            people[key] = sid
        else:
            # Joined to somebody already on the list: fill what the person's
            # file is missing, never overwrite what it has.
            conn.execute(sa.text(
                "UPDATE staff SET phone = COALESCE(phone, :ph), "
                "license_number = COALESCE(license_number, :lic), "
                "note = COALESCE(note, :nt), "
                "position_id = COALESCE(position_id, :p) WHERE id = :id"),
                {"ph": phone, "lic": lic, "nt": notes, "p": pos_id, "id": sid})
            if active:
                conn.execute(sa.text("UPDATE staff SET is_active = :t WHERE id = :id"),
                             {"t": True, "id": sid})
        conn.execute(sa.text("UPDATE operators SET staff_id = :s WHERE id = :o"),
                     {"s": sid, "o": oid})

    # The name written on past records follows the person's spelling, which
    # differs here only by case or spacing ("ibrahima  diallo"): otherwise the
    # driver's history would no longer be found under his name.
    names = {sid: _display(last, first) for sid, last, first in conn.execute(sa.text(
        "SELECT id, last_name, first_name FROM staff")).fetchall()}
    for oid, fid, sid, oldname in conn.execute(sa.text(
            "SELECT id, fleet_id, staff_id, name FROM operators")).fetchall():
        if names.get(sid) and names[sid] != oldname:
            rename_on_records(conn, fid, oldname, names[sid])

    # Two operator rows of one fleet that now point to the same person (the
    # same name typed twice with different case): keep the oldest, move the
    # vehicles that named the other one as their default driver.
    dupes = conn.execute(sa.text(
        "SELECT fleet_id, staff_id, MIN(id) FROM operators "
        "GROUP BY fleet_id, staff_id HAVING COUNT(*) > 1")).fetchall()
    for fid, sid, keep in dupes:
        others = [r[0] for r in conn.execute(sa.text(
            "SELECT id FROM operators WHERE fleet_id = :f AND staff_id = :s AND id <> :k"),
            {"f": fid, "s": sid, "k": keep}).fetchall()]
        for o in others:
            conn.execute(sa.text(
                "UPDATE vehicles SET default_operator_id = :k WHERE default_operator_id = :o"),
                {"k": keep, "o": o})
            conn.execute(sa.text(
                "UPDATE operators SET is_active = :t WHERE id = :k AND "
                "EXISTS (SELECT 1 FROM operators WHERE id = :o AND is_active = :t)"),
                {"t": True, "k": keep, "o": o})
            conn.execute(sa.text("DELETE FROM operators WHERE id = :o"), {"o": o})

    # Logins: tied only when exactly one person bears the name.
    by_key = {}
    for sid, last, first in conn.execute(sa.text(
            "SELECT id, last_name, first_name FROM staff")).fetchall():
        by_key.setdefault(_key(_display(last, first)), []).append(sid)
    taken = set()
    for uid, full_name, phone in conn.execute(sa.text(
            "SELECT id, full_name, phone FROM users WHERE is_super_admin = :f ORDER BY id"),
            {"f": False}).fetchall():
        match = by_key.get(_key(full_name)) or []
        if len(match) == 1 and match[0] not in taken:
            taken.add(match[0])
            conn.execute(sa.text("UPDATE users SET staff_id = :s WHERE id = :u"),
                         {"s": match[0], "u": uid})
            conn.execute(sa.text(
                "UPDATE staff SET phone = COALESCE(phone, :ph) WHERE id = :s"),
                {"ph": phone, "s": match[0]})
            # From now on the login carries the file's name and phone.
            conn.execute(sa.text(
                "UPDATE users SET "
                "full_name = (SELECT TRIM(COALESCE(last_name, '') || ' ' || COALESCE(first_name, '')) "
                "             FROM staff WHERE id = :s), "
                "phone = (SELECT phone FROM staff WHERE id = :s) WHERE id = :u"),
                {"s": match[0], "u": uid})

    with op.batch_alter_table('operators') as b:
        b.alter_column('staff_id', existing_type=sa.Integer(), nullable=False)
        b.create_foreign_key('fk_operators_staff', 'staff', ['staff_id'], ['id'])
        b.drop_constraint('uq_operators_fleet_name', type_='unique')
        b.create_unique_constraint('uq_operators_fleet_staff', ['fleet_id', 'staff_id'])
        b.drop_column('name')
        b.drop_column('phone')
        b.drop_column('license_number')
        b.drop_column('notes')
    op.create_index('ix_operators_staff_id', 'operators', ['staff_id'])


def downgrade():
    op.drop_index('ix_operators_staff_id', 'operators')
    with op.batch_alter_table('operators') as b:
        b.add_column(sa.Column('name', sa.String(120)))
        b.add_column(sa.Column('phone', sa.String(30)))
        b.add_column(sa.Column('license_number', sa.String(40)))
        b.add_column(sa.Column('notes', sa.String(255)))

    op.execute(sa.text("""
        UPDATE operators SET
            name = (SELECT TRIM(COALESCE(s.last_name, '') || ' ' || COALESCE(s.first_name, ''))
                    FROM staff s WHERE s.id = operators.staff_id),
            phone = (SELECT s.phone FROM staff s WHERE s.id = operators.staff_id),
            license_number = (SELECT s.license_number FROM staff s WHERE s.id = operators.staff_id),
            notes = (SELECT s.note FROM staff s WHERE s.id = operators.staff_id)
    """))

    with op.batch_alter_table('operators') as b:
        b.alter_column('name', existing_type=sa.String(120), nullable=False)
        b.drop_constraint('uq_operators_fleet_staff', type_='unique')
        b.drop_constraint('fk_operators_staff', type_='foreignkey')
        b.create_unique_constraint('uq_operators_fleet_name', ['fleet_id', 'name'])
        b.drop_column('staff_id')

    with op.batch_alter_table('users') as b:
        b.drop_constraint('uq_users_staff', type_='unique')
        b.drop_constraint('fk_users_staff', type_='foreignkey')
        b.drop_column('staff_id')

    op.drop_table('person_match_dismissed')
    op.drop_index('ix_staff_documents_staff_id', 'staff_documents')
    op.drop_table('staff_documents')

    with op.batch_alter_table('staff') as b:
        b.drop_constraint('fk_staff_site', type_='foreignkey')
        for col in ('license_expires', 'license_categories', 'license_number', 'company',
                    'cnss_number', 'site_id', 'emergency_phone', 'emergency_name',
                    'address', 'photo_key', 'id_number', 'nationality', 'birth_place',
                    'birth_date', 'sex', 'kind'):
            b.drop_column(col)
