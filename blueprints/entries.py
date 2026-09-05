"""Daily entries blueprint — the core operational logging workflow.

One row per operational period for a vehicle (a trip, a shift, an hour block);
multiple rows per (vehicle, date) are allowed. The form adapts to the vehicle
category: trip-tracked vehicles log *voyages*, hour-tracked ones log *heures*;
both log fuel + km.

Running cumulative_km / cumulative_hours are recomputed chronologically per
vehicle on every change, so they stay correct through edits, deletes, and
back-dated entries — the maintenance rule engine reads these snapshots later.

Scoping goes through the vehicle's fleet (DailyEntry has no fleet_id of its own).
Reuses the shared permission, approval/grace, audit, and modal machinery.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

import maintenance_engine
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, pending_change_exists, require_perm,
                 submit_change)
from models import (DailyEntry, Fleet, Operator, PendingChange, Vehicle,
                    VehicleCategory, db)

entries_bp = Blueprint("entries", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _scoped_entries():
    """Base query joined to Vehicle and filtered to the user's fleets."""
    q = DailyEntry.query.join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q


def _accessible_vehicles(active_only=False):
    # Entry picker: only vehicles on a live fleet (archived fleet = no new saisie).
    q = Vehicle.query.filter(Vehicle.fleet.has(Fleet.is_active.is_(True)))
    if active_only:
        q = q.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _accessible_fleets():
    """Fleets the current user may point on (all, for super admin)."""
    fids = current_user_fleet_ids()
    # Archived fleets drop out of the pickers (no new data on a mothballed
    # fleet); existing data stays visible, scoped by current_user_fleet_ids.
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _accessible_operators():
    q = Operator.query.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Operator.fleet_id.in_(fids))
    return q.order_by(Operator.name).all()


def _get_entry_or_404(eid):
    e = db.session.get(DailyEntry, eid)
    if not e:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and e.vehicle.fleet_id not in fids:
        abort(403)
    return e


def _num(raw, cast):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None, False
    try:
        return cast(raw), False
    except (TypeError, ValueError):
        return None, True


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _valid_month(s):
    try:
        datetime.strptime(s, "%Y-%m")
        return True
    except (TypeError, ValueError):
        return False


def _accessible_categories():
    if current_user_fleet_ids() is None:
        return VehicleCategory.query.order_by(VehicleCategory.sort_order).all()
    codes = set()
    for fl in _accessible_fleets():
        codes.update(fl.categories or [])
    return (VehicleCategory.query.filter(VehicleCategory.code.in_(codes))
            .order_by(VehicleCategory.sort_order).all())


def _recompute_cumulatives(vehicle_id):
    """Re-walk a vehicle's entries in date order and refresh the running
    km / hours snapshots. Caller commits."""
    entries = (DailyEntry.query
               .filter_by(vehicle_id=vehicle_id)
               .order_by(DailyEntry.date, DailyEntry.id)
               .all())
    ck = 0.0
    chh = 0.0
    for e in entries:
        ck += e.kilometers or 0
        chh += e.hours or 0
        e.cumulative_km = ck
        e.cumulative_hours = chh


def _read_entry_form(entry):
    """Validate the form into a column dict. Returns (data, None) or (None, err).

    The unit field (trips vs hours) is chosen server-side from the vehicle's
    category, so the client can't write hours onto a trip-tracked vehicle.
    """
    t = get_t()
    vehicle_id = request.form.get("vehicle_id", type=int)
    date_str = (request.form.get("date") or "").strip()

    if not vehicle_id:
        return None, t["entry.err.vehicle_required"]
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return None, t["entry.err.vehicle_required"]
    fids = current_user_fleet_ids()
    if fids is not None and vehicle.fleet_id not in fids:
        return None, t["error.forbidden"]
    if not date_str or not _valid_date(date_str):
        return None, t["entry.err.date_required"]

    km, e1 = _num(request.form.get("kilometers"), float)
    trips = hours = index_start = index_end = None
    bad = False
    tracking = vehicle.category.tracking
    # Kilometres only apply to a moving vehicle (voyages). A machine tracked by
    # hours / index never runs km, so drop any value the form might still carry.
    if tracking != "trips":
        km = None
    if tracking == "trips":
        trips, bad = _num(request.form.get("trips"), lambda s: int(round(float(s))))
    elif tracking == "hours_index":
        # Hour-meter readings; worked hours = end - start.
        index_start, es = _num(request.form.get("index_start"), float)
        index_end, ee = _num(request.form.get("index_end"), float)
        bad = es or ee
        if not bad and index_start is not None and index_end is not None:
            if index_end < index_start:
                return None, t.get("entry.err.index_order",
                                   "L'index de fin doit être supérieur ou égal à l'index de début.")
            hours = round(index_end - index_start, 2)
    else:  # "hours" — direct entry
        hours, bad = _num(request.form.get("hours"), float)
    if e1 or bad:
        return None, t["entry.err.bad_number"]
    # A negative count parses fine but is never a real saisie.
    if any(v is not None and v < 0
           for v in (km, trips, hours, index_start, index_end)):
        return None, t["entry.err.negative"]

    data = dict(
        vehicle_id=vehicle_id,
        date=date_str,
        kilometers=km,
        trips=trips,
        hours=hours,
        index_start=index_start,
        index_end=index_end,
        operator=(request.form.get("operator") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    )
    return data, None


def _form_context(entry):
    preset_date = request.args.get("date")
    if not (preset_date and _valid_date(preset_date)):
        preset_date = None
    # When opening the form for a specific vehicle (e.g. from the roster),
    # pre-fill its default driver if it has one.
    preset_vehicle = request.args.get("vehicle_id", type=int)
    preset_operator = ""
    if preset_vehicle and entry is None:
        v = db.session.get(Vehicle, preset_vehicle)
        if v and v.default_operator and v.default_operator.is_active:
            preset_operator = v.default_operator.name
    return {
        "vehicles": _accessible_vehicles(active_only=True),
        "operators": _accessible_operators(),
        "preset_vehicle": preset_vehicle,
        "preset_operator": preset_operator,
        "preset_date": preset_date,
        "today": date.today().isoformat(),
    }


def _render_entry_form(entry, error=None):
    tpl = "_entry_form.html" if is_modal_request() else "entry_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, entry=entry, error=error, **_form_context(entry)), status


# ── Routes ───────────────────────────────────────────────────────────────────


def _filter_values():
    return {
        "fleet_id": request.args.get("fleet_id", type=int),
        "vehicle_id": request.args.get("vehicle_id", type=int),
        "category_id": request.args.get("category_id", type=int),
        "operator": (request.args.get("operator") or "").strip(),
        "month": request.args.get("month") or "",
        "date_from": request.args.get("date_from") or "",
        "date_to": request.args.get("date_to") or "",
    }


def _apply_filters(q):
    """Apply the daily-entry filters from request.args to a Vehicle-joined query.
    A month takes precedence over the date range (batmex-style)."""
    f = _filter_values()
    if f["fleet_id"]:
        q = q.filter(Vehicle.fleet_id == f["fleet_id"])
    if f["category_id"]:
        q = q.filter(Vehicle.category_id == f["category_id"])
    if f["vehicle_id"]:
        q = q.filter(DailyEntry.vehicle_id == f["vehicle_id"])
    if f["operator"]:
        q = q.filter(DailyEntry.operator == f["operator"])
    if f["month"] and _valid_month(f["month"]):
        q = q.filter(DailyEntry.date.like(f["month"] + "-%"))
    else:
        if f["date_from"] and _valid_date(f["date_from"]):
            q = q.filter(DailyEntry.date >= f["date_from"])
        if f["date_to"] and _valid_date(f["date_to"]):
            q = q.filter(DailyEntry.date <= f["date_to"])
    return q


def _ghost_create(pc):
    """A pending-creation row preview (no DB row exists yet) from its payload."""
    p = pc.payload or {}
    v = db.session.get(Vehicle, p.get("vehicle_id")) if p.get("vehicle_id") else None
    return {"date": p.get("date"), "vehicle": v, "trips": p.get("trips"),
            "hours": p.get("hours"), "kilometers": p.get("kilometers"),
            "operator": p.get("operator")}


@entries_bp.route("/entries")
@login_required
@require_perm("entry.view")
def index():
    # Paged rather than capped: one row per vehicle per day fills 500 in under
    # three weeks on a fleet of thirty, and the rows past the cap simply were
    # not there, with nothing on the page to say so.
    pagination = (_apply_filters(_scoped_entries())
                  .order_by(DailyEntry.date.desc(), DailyEntry.id.desc())
                  .paginate(page=request.args.get("page", 1, type=int),
                            per_page=PER_PAGE, error_out=False))
    entries = pagination.items
    # The requester's own in-flight changes, so rows show "… en attente" and
    # locked actions, and pending creations appear as ghost rows on top.
    mine = (PendingChange.query
            .filter_by(requested_by=current_user.id, status="pending",
                       resource_type="daily_entry").all())
    pending_map = {pc.resource_id: pc.action for pc in mine
                   if pc.action in ("update", "delete") and pc.resource_id}
    ghost_creates = [_ghost_create(pc) for pc in mine if pc.action == "create"]
    return render_template("entries.html", entries=entries, pagination=pagination,
                           fleets=_accessible_fleets(), vehicles=_accessible_vehicles(),
                           categories=_accessible_categories(),
                           operators=_accessible_operators(), f=_filter_values(),
                           pending_map=pending_map, ghost_creates=ghost_creates)


PER_PAGE = 50         # rows on one screen
EXPORT_LIMIT = 1000   # hard cap on rows in one export; flagged on the document


def _export_context():
    """Rows + headings for the printable pointage, from the current filters."""
    t = get_t()
    # Eager-load vehicle + category: the template touches both on every row, so
    # without this a 1000-row export fires ~2000 extra queries.
    q = _apply_filters(_scoped_entries())
    entries = (q.options(joinedload(DailyEntry.vehicle).joinedload(Vehicle.category))
               .order_by(DailyEntry.date.desc(), DailyEntry.id.desc())
               .limit(EXPORT_LIMIT).all())
    # Summed over the whole selection, not over the rows the document could
    # hold: past the cap the figures at the foot would quietly come up short.
    co = db.func.coalesce
    total_km, total_trips, total_hours = q.with_entities(
        co(db.func.sum(DailyEntry.kilometers), 0),
        co(db.func.sum(DailyEntry.trips), 0),
        co(db.func.sum(DailyEntry.hours), 0.0)).one()

    f = _filter_values()
    parts = []
    if f["fleet_id"]:
        fl = db.session.get(Fleet, f["fleet_id"])
        if fl:
            parts.append(fl.name)
    if f["category_id"]:
        cat = db.session.get(VehicleCategory, f["category_id"])
        if cat:
            parts.append(cat.label_fr or cat.label)
    if f["vehicle_id"]:
        v = db.session.get(Vehicle, f["vehicle_id"])
        if v:
            parts.append(v.code)
    if f["operator"]:
        parts.append(f["operator"])
    if f["month"] and _valid_month(f["month"]):
        parts.append(f["month"])
    elif f["date_from"] or f["date_to"]:
        parts.append("%s → %s" % (f["date_from"] or "…", f["date_to"] or "…"))
    subtitle = " · ".join(parts) if parts else t["export.all_vehicles"]

    return dict(
        entries=entries, subtitle=subtitle,
        truncated=len(entries) >= EXPORT_LIMIT,
        total_km=total_km, total_trips=total_trips, total_hours=total_hours,
        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))


@entries_bp.route("/entries/export.print")
@login_required
@require_perm("report.export_pdf")
def export_print():
    """The printable pointage, rendered as HTML for the browser to print.

    Replaced a server-side WeasyPrint PDF, which spent seconds laying the
    document out and then shipped a few hundred KB that gzip can't touch — too
    slow over the link from Guinea. Filters come from the query string, so what
    prints never depends on what the data page happens to be showing.
    """
    return render_template("entries_print.html",
                           back_url=url_for("entries.index", **request.args.to_dict()),
                           **_export_context())


@entries_bp.route("/roster")
@login_required
@require_perm("entry.view")
def roster_index():
    """Land on the roster for the user's first accessible fleet."""
    fleets = _accessible_fleets()
    if not fleets:
        return render_template("roster.html", fleet=None, fleets=[], vehicles=[],
                               by_vehicle={}, date_str=date.today().isoformat())
    return redirect(url_for("entries.roster", slug=fleets[0].slug))


@entries_bp.route("/roster/<slug>")
@login_required
@require_perm("entry.view")
def roster(slug):
    """Daily pointage sheet for one fleet — one cell per vehicle, scoped so a
    user never sees vehicles outside the fleets they're assigned to."""
    fleet = Fleet.query.filter_by(slug=slug).first()
    if not fleet:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and fleet.id not in fids:
        abort(403)
    if not fleet.is_active:
        # Archived fleet: no pointing. Its past entries stay in "Saisies".
        flash("info|" + get_t().get("roster.fleet_archived",
              "Cette flotte est archivée — saisie impossible. Son historique reste dans les saisies."))
        return redirect(url_for("entries.roster_index"))

    date_str = request.args.get("date") or date.today().isoformat()
    if not _valid_date(date_str):
        date_str = date.today().isoformat()

    vehicles = (Vehicle.query.filter_by(fleet_id=fleet.id, is_active=True)
                .order_by(Vehicle.code).all())
    by_vehicle = {}
    if vehicles:
        rows = (DailyEntry.query
                .filter(DailyEntry.vehicle_id.in_([v.id for v in vehicles]),
                        DailyEntry.date == date_str)
                .order_by(DailyEntry.id).all())
        for e in rows:
            by_vehicle.setdefault(e.vehicle_id, []).append(e)

    done = sum(1 for v in vehicles if v.id in by_vehicle)

    # The requester's own in-flight changes on this sheet: lock pending edits/
    # deletes and surface pending creations as ghost entries in the cell.
    vids = {v.id for v in vehicles}
    mine = (PendingChange.query
            .filter_by(requested_by=current_user.id, status="pending",
                       resource_type="daily_entry").all())
    pending_map = {pc.resource_id: pc.action for pc in mine
                   if pc.action in ("update", "delete") and pc.resource_id}
    ghost_by_vehicle = {}
    for pc in mine:
        if pc.action == "create":
            p = pc.payload or {}
            if p.get("date") == date_str and p.get("vehicle_id") in vids:
                ghost_by_vehicle.setdefault(p["vehicle_id"], []).append(_ghost_create(pc))

    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return render_template(
        "roster.html", fleet=fleet, fleets=_accessible_fleets(),
        vehicles=vehicles, by_vehicle=by_vehicle, date_str=date_str,
        prev_date=(d - timedelta(days=1)).isoformat(),
        next_date=(d + timedelta(days=1)).isoformat(),
        done=done, pending=len(vehicles) - done,
        pending_map=pending_map, ghost_by_vehicle=ghost_by_vehicle,
    )


@entries_bp.route("/entries/new", methods=["GET", "POST"])
@login_required
@require_perm("entry.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_entry_form(None)
        if error:
            return _render_entry_form(None, error)
        veh = db.session.get(Vehicle, data["vehicle_id"])
        if veh and veh.fleet and not veh.fleet.is_active:
            return _render_entry_form(None, t.get("roster.fleet_archived",
                "Cette flotte est archivée — saisie impossible. Son historique reste dans les saisies."))
        fleet_id = veh.fleet_id
        if needs_approval("entry.create"):
            submit_change(resource_type="daily_entry", action="create",
                          fleet_id=fleet_id, payload=data)
            flash("success|" + t["entry.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("entries.index"))
        e = DailyEntry(created_by=current_user.id, **data)
        db.session.add(e)
        _recompute_cumulatives(e.vehicle_id)
        db.session.flush()
        maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, e.vehicle_id))
        log_action("CREATE", "daily_entry", resource_id=e.id, fleet_id=fleet_id,
                   detail=f"Logged entry for vehicle #{e.vehicle_id} on {e.date}")
        db.session.commit()
        flash("success|" + t["entry.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("entries.index"))
    return _render_entry_form(None)


@entries_bp.route("/entries/<int:eid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("entry.edit")
def edit(eid):
    entry = _get_entry_or_404(eid)
    t = get_t()
    if request.method == "POST":
        if pending_change_exists("daily_entry", entry.id):
            return _render_entry_form(entry, t["entry.err.pending_exists"])
        data, error = _read_entry_form(entry)
        if error:
            return _render_entry_form(entry, error)
        fleet_id = db.session.get(Vehicle, data["vehicle_id"]).fleet_id
        if needs_approval("entry.edit", entry.created_by, entry.created_at):
            submit_change(resource_type="daily_entry", action="update",
                          resource_id=entry.id, fleet_id=fleet_id, payload=data)
            flash("success|" + t["entry.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("entries.index"))
        old_vehicle = entry.vehicle_id
        for k, val in data.items():
            setattr(entry, k, val)
        # Refresh running totals for the affected vehicle(s).
        _recompute_cumulatives(entry.vehicle_id)
        if old_vehicle != entry.vehicle_id:
            _recompute_cumulatives(old_vehicle)
        maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, entry.vehicle_id))
        log_action("UPDATE", "daily_entry", resource_id=entry.id, fleet_id=fleet_id,
                   detail=f"Edited entry #{entry.id}")
        db.session.commit()
        flash("success|" + t["entry.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("entries.index"))
    return _render_entry_form(entry)


@entries_bp.route("/entries/<int:eid>/delete", methods=["POST"])
@login_required
@require_perm("entry.delete")
def delete(eid):
    entry = _get_entry_or_404(eid)
    t = get_t()
    vid, fleet_id = entry.vehicle_id, entry.vehicle.fleet_id
    if pending_change_exists("daily_entry", entry.id):
        flash("error|" + t["entry.err.pending_exists"])
        return redirect(request.referrer or url_for("entries.index"))
    if needs_approval("entry.delete", entry.created_by, entry.created_at):
        submit_change(resource_type="daily_entry", action="delete",
                      resource_id=entry.id, fleet_id=fleet_id, payload={})
        flash("success|" + t["entry.submitted"])
        return redirect(request.referrer or url_for("entries.index"))
    db.session.delete(entry)
    db.session.flush()
    _recompute_cumulatives(vid)
    maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, vid))
    log_action("DELETE", "daily_entry", resource_id=eid, fleet_id=fleet_id,
               detail=f"Deleted entry #{eid}")
    db.session.commit()
    flash("success|" + t["entry.deleted"])
    return redirect(request.referrer or url_for("entries.index"))
