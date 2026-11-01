"""Conducteurs -- the people who drive, seen from the fleets.

Not a list of its own any more: a driver is a person (blueprints.staff) and
the fleets he drives for are Operator rows ticked on his file. This page lists
those people, and each fleet's driver sheet (activity, vehicles, costs) still
lives here. Read only: people are created and edited on the RH side only.
"""
from datetime import datetime

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from app import current_user_fleet_ids, require_perm
from models import (Citerne, DailyEntry, Expense, Fleet, FuelMovement,
                    MaintenanceRecord, Operator, Staff, Vehicle, db)

operators_bp = Blueprint("operators", __name__)

PER_PAGE = 50   # rows on one screen


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    fids = current_user_fleet_ids()
    # Archived fleets drop out of the pickers (no new data on a mothballed
    # fleet); existing data stays visible, scoped by current_user_fleet_ids.
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _get_operator_or_404(oid):
    op = db.session.get(Operator, oid)
    if not op:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and op.fleet_id not in fids:
        abort(403)
    return op


def operator_usage(op):
    """What still refers to this person driving for this fleet, and whether
    the link can be deleted.

    Entries, services, fuel movements and costs link the person (driver_id);
    anything recorded under him in this fleet blocks, and the link is archived
    instead. Being a vehicle's default driver blocks too: silently clearing
    someone else's default is a surprise, and the fix is one edit away on the
    vehicle.
    """
    sid, fid = op.staff_id, op.fleet_id
    fleet_vehicles = db.session.query(Vehicle.id).filter(Vehicle.fleet_id == fid)
    usage = {
        "entries": (DailyEntry.query
                    .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
                    .filter(Vehicle.fleet_id == fid, DailyEntry.driver_id == sid).count()),
        "records": (MaintenanceRecord.query
                    .join(Vehicle, MaintenanceRecord.vehicle_id == Vehicle.id)
                    .filter(Vehicle.fleet_id == fid,
                            MaintenanceRecord.driver_id == sid).count()),
        # A fill hangs off a machine or a tank of the fleet; a cost off the
        # fleet or one of its machines.
        "movements": FuelMovement.query.filter(
            FuelMovement.driver_id == sid,
            db.or_(FuelMovement.vehicle_id.in_(fleet_vehicles),
                   FuelMovement.citerne_id.in_(
                       db.session.query(Citerne.id).filter(Citerne.fleet_id == fid)))).count(),
        "expenses": Expense.query.filter(
            Expense.driver_id == sid,
            db.or_(Expense.fleet_id == fid, Expense.vehicle_id.in_(fleet_vehicles))).count(),
        # Live machines only. A deleted machine that logged something keeps a
        # tombstone row, and that row keeps its default_operator_id — so a
        # driver would stay pinned by a vehicle nobody can see any more.
        "default_for": (Vehicle.query
                        .filter_by(default_operator_id=op.id)
                        .filter(Vehicle.deleted_at.is_(None)).count()),
    }
    usage["blockers"] = [k for k, n in usage.items() if n]
    usage["deletable"] = not usage["blockers"]
    return usage


@operators_bp.route("/operators")
@login_required
@require_perm("operator.view")
def index():
    """Everybody who drives for a fleet this user sees, one row per person
    with the fleets listed. Archived = no longer drives anywhere visible, or
    the person himself has left."""
    from blueprints.staff import licence_state
    fleets = _accessible_fleets()
    active_fleet = request.args.get("fleet", type=int)
    show_archived = request.args.get("archived") == "1"
    fids = current_user_fleet_ids()

    links = Operator.query
    if fids is not None:
        links = links.filter(Operator.fleet_id.in_(fids))
    if active_fleet:
        links = links.filter(Operator.fleet_id == active_fleet)
    live = links.filter(Operator.is_active.is_(True))

    q = Staff.query
    if show_archived:
        q = q.filter(Staff.id.in_(links.with_entities(Operator.staff_id)),
                     db.or_(Staff.is_active.is_(False),
                            Staff.id.notin_(live.with_entities(Operator.staff_id))))
    else:
        q = q.filter(Staff.is_active.is_(True),
                     Staff.id.in_(live.with_entities(Operator.staff_id)))
    search = (request.args.get("q") or "").strip()
    if search:
        like = "%" + search + "%"
        q = q.filter(db.or_(Staff.last_name.ilike(like), Staff.first_name.ilike(like),
                            Staff.company.ilike(like)))
    pagination = q.order_by(Staff.last_name, Staff.first_name).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE,
        error_out=False)
    people = pagination.items

    seen = None if fids is None else set(fids)
    rows = []
    for p in people:
        own = [o for o in p.drives if seen is None or o.fleet_id in seen]
        rows.append({
            "p": p,
            "links": sorted([o for o in own if o.is_active] or own,
                            key=lambda o: o.fleet.name),
            "licence": licence_state(p),
        })

    archived_q = Staff.query.filter(
        Staff.id.in_(links.with_entities(Operator.staff_id)),
        db.or_(Staff.is_active.is_(False),
               Staff.id.notin_(live.with_entities(Operator.staff_id))))
    return render_template("operators.html", rows=rows,
                           pagination=pagination, search=search,
                           filter_fleets=fleets, active_fleet=active_fleet,
                           show_archived=show_archived,
                           archived_count=archived_q.count())


@operators_bp.route("/operators/<int:oid>")
@login_required
@require_perm("operator.view")
def detail(oid):
    """The old per-fleet driver sheet: now the person's page, on that fleet."""
    op = _get_operator_or_404(oid)
    return redirect(url_for("operators.person", pid=op.staff_id, fleet=op.fleet_id))


@operators_bp.route("/operators/personne/<int:pid>")
@login_required
@require_perm("operator.view")
def person(pid):
    """The operations door to a person: what he does on the ground -- his
    pointing, the machines he drives, the services, the fuel and the costs
    tied to him -- on one of his fleets or all of them, for a month.
    Who he is and on what terms he works is the RH door (staff.profile)."""
    from blueprints.staff import licence_state
    p = db.session.get(Staff, pid)
    if not p:
        abort(404)
    fids = current_user_fleet_ids()
    links = [o for o in p.drives if fids is None or o.fleet_id in fids]
    if not links:
        abort(404)
    links.sort(key=lambda o: (not o.is_active, o.fleet.name))
    fleet = request.args.get("fleet", type=int)
    if fleet not in {o.fleet_id for o in links}:
        fleet = None
    scope = [fleet] if fleet else [o.fleet_id for o in links]

    # The figures on top are this month's. The month chosen in the tabs'
    # section (fuel, costs) is that section's alone.
    this_month = datetime.utcnow().strftime("%Y-%m")
    month = request.args.get("month", "")
    try:
        datetime.strptime(month, "%Y-%m")
    except (TypeError, ValueError):
        month = this_month

    fleet_vehicles = db.session.query(Vehicle.id).filter(Vehicle.fleet_id.in_(scope))

    def entries():
        return (DailyEntry.query.join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
                .filter(Vehicle.fleet_id.in_(scope), DailyEntry.driver_id == pid))

    co = func.coalesce
    n_pointages, tot_trips, tot_hours, tot_km = (
        entries().with_entities(
            func.count(DailyEntry.id),
            co(func.sum(DailyEntry.trips), 0),
            co(func.sum(DailyEntry.hours), 0.0),
            co(func.sum(DailyEntry.kilometers), 0.0))
        .filter(DailyEntry.date.like(this_month + "%")).one())
    last_date = entries().with_entities(func.max(DailyEntry.date)).scalar()

    # Vehicles driven (all-time): default-driver assignments and machines pointed.
    link_ids = [o.id for o in links if o.fleet_id in scope]
    default_ids = {v.id for v in Vehicle.query.filter(
        Vehicle.default_operator_id.in_(link_ids)).all()} if link_ids else set()
    pointed_ids = {r[0] for r in entries().with_entities(DailyEntry.vehicle_id).distinct()}
    veh_ids = default_ids | pointed_ids
    vehicles = (Vehicle.query.filter(Vehicle.id.in_(veh_ids)).order_by(Vehicle.code).all()
                if veh_ids else [])

    records = (MaintenanceRecord.query
               .join(Vehicle, MaintenanceRecord.vehicle_id == Vehicle.id)
               .filter(Vehicle.fleet_id.in_(scope), MaintenanceRecord.driver_id == pid)
               .order_by(MaintenanceRecord.date.desc()).limit(50).all())

    # Fuel he took: fills on the fleet's machines or tanks -- this month for
    # the figure on top, the chosen month for the tab.
    def fuel_in(m):
        return FuelMovement.query.filter(
            FuelMovement.driver_id == pid, FuelMovement.date.like(m + "%"),
            db.or_(FuelMovement.vehicle_id.in_(fleet_vehicles),
                   FuelMovement.citerne_id.in_(
                       db.session.query(Citerne.id).filter(Citerne.fleet_id.in_(scope)))))
    fuel_litres = fuel_in(this_month).with_entities(co(func.sum(FuelMovement.liters), 0)).scalar()
    fuel_q = fuel_in(month)
    fuel_month_litres = fuel_q.with_entities(co(func.sum(FuelMovement.liters), 0)).scalar()
    fuel_rows = fuel_q.order_by(FuelMovement.date.desc(), FuelMovement.id.desc()).all()

    # Costs tied to him, stacked by category over the 6 months ending at the
    # selected month.
    from blueprints.expenses import category_label
    y, mo = int(month[:4]), int(month[5:7])
    months = []
    for i in range(5, -1, -1):
        mm, yy = mo - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append("%04d-%02d" % (yy, mm))
    idx = {m: i for i, m in enumerate(months)}
    ym = func.substr(Expense.date, 1, 7)
    rows = (db.session.query(ym, Expense.category, func.sum(Expense.amount))
            .filter(Expense.driver_id == pid, ym.in_(months),
                    db.or_(Expense.fleet_id.in_(scope), Expense.vehicle_id.in_(fleet_vehicles)))
            .group_by(ym, Expense.category).all())
    series = {}
    for m, cat, amt in rows:
        if m in idx:
            series.setdefault(cat, [0] * len(months))[idx[m]] = int(amt or 0)
    chart_series = [{"cat": c, "label": category_label(c), "data": series[c]}
                    for c in sorted(series) if any(series[c])]

    tabs = ["vehicles", "records", "fuel", "costs"]
    tab = request.args.get("tab") if request.args.get("tab") in tabs else "vehicles"
    return render_template(
        "operator_person.html", p=p, links=links, fleet=fleet, month=month,
        tabs=tabs, tab=tab,
        licence=licence_state(p),
        n_pointages=n_pointages, tot_trips=int(tot_trips or 0),
        tot_hours=float(tot_hours or 0), tot_km=float(tot_km or 0),
        last_date=last_date, vehicles=vehicles, default_ids=default_ids,
        records=records, fuel_litres=float(fuel_litres or 0), fuel_rows=fuel_rows,
        fuel_month_litres=float(fuel_month_litres or 0),
        expense_chart={"months": months, "series": chart_series})


def legacy_operator_payload(payload):
    """A driver change asked for before drivers became people, approved now.

    Its name, phone, licence and notes go to the person's file -- the person
    of that name, or a new one -- and the row keeps only the link."""
    payload = dict(payload)
    name = " ".join((payload.pop("name", None) or "").split())
    phone = payload.pop("phone", None)
    licence = payload.pop("license_number", None)
    notes = payload.pop("notes", None)
    if name and "staff_id" not in payload:
        key = name.lower()
        person = next((p for p in Staff.query.all()
                       if " ".join(p.name.lower().split()) == key), None)
        if person is None:
            person = Staff(last_name=name[:80], kind="employee")
            db.session.add(person)
            db.session.flush()
        person.phone = person.phone or phone
        person.license_number = person.license_number or licence
        person.note = person.note or notes
        payload["staff_id"] = person.id
    return payload
