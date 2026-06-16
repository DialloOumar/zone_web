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

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import maintenance_engine
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, submit_change)
from models import DailyEntry, Fleet, Operator, Vehicle, db

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
    q = Vehicle.query
    if active_only:
        q = q.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _accessible_fleets():
    """Fleets the current user may point on (all, for super admin)."""
    fids = current_user_fleet_ids()
    q = Fleet.query.order_by(Fleet.name)
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
    trips = hours = None
    if vehicle.category.unit_type == "trips":
        trips, e2 = _num(request.form.get("trips"), lambda s: int(round(float(s))))
    else:
        hours, e2 = _num(request.form.get("hours"), float)
    if e1 or e2:
        return None, t["entry.err.bad_number"]

    data = dict(
        vehicle_id=vehicle_id,
        date=date_str,
        kilometers=km,
        trips=trips,
        hours=hours,
        operator=(request.form.get("operator") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    )
    return data, None


def _form_context(entry):
    preset_date = request.args.get("date")
    if not (preset_date and _valid_date(preset_date)):
        preset_date = None
    return {
        "vehicles": _accessible_vehicles(active_only=True),
        "operators": _accessible_operators(),
        "preset_vehicle": request.args.get("vehicle_id", type=int),
        "preset_date": preset_date,
        "today": date.today().isoformat(),
    }


def _render_entry_form(entry, error=None):
    tpl = "_entry_form.html" if is_modal_request() else "entry_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, entry=entry, error=error, **_form_context(entry)), status


# ── Routes ───────────────────────────────────────────────────────────────────


@entries_bp.route("/entries")
@login_required
@require_perm("entry.view")
def index():
    vehicles = _accessible_vehicles()
    fv = request.args.get("vehicle_id", type=int)
    fd = request.args.get("date") or ""

    q = _scoped_entries()
    if fv:
        q = q.filter(DailyEntry.vehicle_id == fv)
    if fd and _valid_date(fd):
        q = q.filter(DailyEntry.date == fd)
    entries = q.order_by(DailyEntry.date.desc(), DailyEntry.id.desc()).limit(200).all()

    return render_template("entries.html", entries=entries, vehicles=vehicles,
                           fv=fv, fd=fd)


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
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return render_template(
        "roster.html", fleet=fleet, fleets=_accessible_fleets(),
        vehicles=vehicles, by_vehicle=by_vehicle, date_str=date_str,
        prev_date=(d - timedelta(days=1)).isoformat(),
        next_date=(d + timedelta(days=1)).isoformat(),
        done=done, pending=len(vehicles) - done,
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
        fleet_id = db.session.get(Vehicle, data["vehicle_id"]).fleet_id
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
    db.session.delete(entry)
    db.session.flush()
    _recompute_cumulatives(vid)
    log_action("DELETE", "daily_entry", resource_id=eid, fleet_id=fleet_id,
               detail=f"Deleted entry #{eid}")
    db.session.commit()
    flash("success|" + t["entry.deleted"])
    return redirect(request.referrer or url_for("entries.index"))
