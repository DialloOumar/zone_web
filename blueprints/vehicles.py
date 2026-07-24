"""Vehicles blueprint — the operational keystone.

List / create / edit / detail / delete for vehicles, scoped to the user's
fleets. First module to wire the approval+grace flow: when a user's role
marks vehicle.* as requiring approval (and they're outside the grace window),
the change is parked in the approval queue instead of applied.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, scoped, submit_change)
from models import (Alert, Citerne, DailyEntry, Expense, Fleet,
                    MaintenanceRecord, Operator, Vehicle, VehicleCategory, db)

vehicles_bp = Blueprint("vehicles", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    """Fleets the current user may file vehicles under (all, for super admin)."""
    fids = current_user_fleet_ids()
    q = Fleet.query.order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _get_vehicle_or_404(vid):
    v = db.session.get(Vehicle, vid)
    if not v:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and v.fleet_id not in fids:
        abort(403)
    return v


def _num(raw, cast):
    """Parse an optional numeric field. Returns (value, had_error)."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None, False
    try:
        return cast(raw), False
    except (TypeError, ValueError):
        return None, True


def _read_vehicle_form(vehicle):
    """Validate + normalise the form into a column dict.

    Returns (data, None) on success or (None, error_message) on failure.
    """
    t = get_t()
    code = (request.form.get("code") or "").strip()
    fleet_id = request.form.get("fleet_id", type=int)
    category_id = request.form.get("category_id", type=int)

    if not code:
        return None, t["vehicle.err.code_required"]
    if not fleet_id:
        return None, t["vehicle.err.fleet_required"]
    if not category_id:
        return None, t["vehicle.err.category_required"]

    # Fleet must be one the user can access.
    fids = current_user_fleet_ids()
    if fids is not None and fleet_id not in fids:
        return None, t["error.forbidden"]
    fleet = db.session.get(Fleet, fleet_id)
    category = db.session.get(VehicleCategory, category_id)
    if not fleet:
        return None, t["vehicle.err.fleet_required"]
    if not category:
        return None, t["vehicle.err.category_required"]
    # Keep data consistent: the category must belong to the chosen fleet.
    if category.code not in (fleet.categories or []):
        return None, t["vehicle.err.cat_not_in_fleet"]

    # Code unique (case-insensitive), excluding self on edit.
    q = Vehicle.query.filter(db.func.lower(Vehicle.code) == code.lower())
    if vehicle:
        q = q.filter(Vehicle.id != vehicle.id)
    if q.first():
        return None, t["vehicle.err.code_taken"]

    baseline, err1 = _num(request.form.get("baseline_l_per_unit_override"), float)
    if err1:
        return None, t["vehicle.err.bad_number"]

    # Optional default driver — must be a registered operator of this fleet.
    default_operator_id = request.form.get("default_operator_id", type=int) or None
    if default_operator_id:
        op = db.session.get(Operator, default_operator_id)
        if not op or op.fleet_id != fleet_id:
            return None, t.get("vehicle.err.operator_fleet",
                               "Le conducteur par défaut doit appartenir à la flotte.")

    data = dict(
        code=code,
        fleet_id=fleet_id,
        category_id=category_id,
        description=(request.form.get("description") or "").strip() or None,
        site=(request.form.get("site") or "").strip() or None,
        baseline_l_per_unit_override=baseline,
        default_operator_id=default_operator_id,
        is_active=request.form.get("is_active") is not None,
    )
    return data, None


def _accessible_operators():
    q = Operator.query.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Operator.fleet_id.in_(fids))
    return q.order_by(Operator.name).all()


def _form_context(vehicle):
    return {
        "fleets": _accessible_fleets(),
        "categories": VehicleCategory.query.order_by(VehicleCategory.sort_order).all(),
        "operators": _accessible_operators(),
        "form_active": (request.form.get("is_active") is not None)
        if request.method == "POST"
        else (vehicle.is_active if vehicle else True),
    }


# ── Routes ───────────────────────────────────────────────────────────────────


@vehicles_bp.route("/vehicles")
@login_required
@require_perm("vehicle.view")
def index():
    cats = VehicleCategory.query.order_by(VehicleCategory.sort_order).all()
    cat_by_code = {c.code: c for c in cats}
    active_code = request.args.get("category") or ""
    show_archived = request.args.get("archived") == "1"

    q = scoped(Vehicle).filter(Vehicle.is_active.is_(not show_archived))
    if active_code and active_code in cat_by_code:
        q = q.filter(Vehicle.category_id == cat_by_code[active_code].id)
    vehicles = q.order_by(Vehicle.code).all()
    archived_count = scoped(Vehicle).filter(Vehicle.is_active.is_(False)).count()

    # Filter chips: only categories present in the user's accessible fleets.
    allowed = set()
    for f in _accessible_fleets():
        allowed.update(f.categories or [])
    filter_cats = [c for c in cats if c.code in allowed] or cats

    return render_template("vehicles.html", vehicles=vehicles,
                           filter_cats=filter_cats, active_code=active_code,
                           show_archived=show_archived, archived_count=archived_count)


@vehicles_bp.route("/vehicles/<int:vid>")
@login_required
@require_perm("vehicle.view")
def detail(vid):
    vehicle = _get_vehicle_or_404(vid)
    entries = (DailyEntry.query.filter_by(vehicle_id=vid)
               .order_by(DailyEntry.date.desc(), DailyEntry.id.desc())
               .limit(10).all())
    expenses = (Expense.query.filter_by(vehicle_id=vid)
                .order_by(Expense.date.desc(), Expense.id.desc())
                .limit(10).all())
    records = (MaintenanceRecord.query.filter_by(vehicle_id=vid)
               .order_by(MaintenanceRecord.date.desc(), MaintenanceRecord.id.desc())
               .limit(10).all())
    alerts = (Alert.query.filter_by(vehicle_id=vid)
              .filter(Alert.status.in_(("open", "snoozed")))
              .order_by(Alert.triggered_at.desc()).all())
    # If this vehicle is a citerne's tanker, surface the link back to its flow.
    citerne = Citerne.query.filter_by(vehicle_id=vid).first()
    return render_template("vehicle_detail.html", vehicle=vehicle,
                           entries=entries, expenses=expenses,
                           records=records, alerts=alerts, citerne=citerne)


def _render_vehicle_form(vehicle, error=None):
    """Render the vehicle form as a modal partial or a full page."""
    tpl = "_vehicle_form.html" if is_modal_request() else "vehicle_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, vehicle=vehicle, error=error,
                           **_form_context(vehicle)), status


@vehicles_bp.route("/vehicles/new", methods=["GET", "POST"])
@login_required
@require_perm("vehicle.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_vehicle_form(None)
        if error:
            return _render_vehicle_form(None, error)
        if needs_approval("vehicle.create"):
            submit_change(resource_type="vehicle", action="create",
                          fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["vehicle.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("vehicles.index"))
        v = Vehicle(created_by=current_user.id, **data)
        db.session.add(v)
        db.session.flush()
        log_action("CREATE", "vehicle", resource_id=v.id, fleet_id=v.fleet_id,
                   detail=f"Created vehicle '{v.code}'")
        db.session.commit()
        flash("success|" + t["vehicle.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("vehicles.detail", vid=v.id))
    return _render_vehicle_form(None)


@vehicles_bp.route("/vehicles/<int:vid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("vehicle.edit")
def edit(vid):
    vehicle = _get_vehicle_or_404(vid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_vehicle_form(vehicle)
        if error:
            return _render_vehicle_form(vehicle, error)
        if needs_approval("vehicle.edit", vehicle.created_by, vehicle.created_at):
            submit_change(resource_type="vehicle", action="update",
                          resource_id=vehicle.id, fleet_id=data["fleet_id"],
                          payload=data)
            flash("success|" + t["vehicle.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("vehicles.detail", vid=vehicle.id))
        for k, val in data.items():
            setattr(vehicle, k, val)
        log_action("UPDATE", "vehicle", resource_id=vehicle.id,
                   fleet_id=vehicle.fleet_id, detail=f"Updated vehicle '{vehicle.code}'")
        db.session.commit()
        flash("success|" + t["vehicle.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("vehicles.detail", vid=vehicle.id))
    return _render_vehicle_form(vehicle)


@vehicles_bp.route("/vehicles/<int:vid>/delete", methods=["POST"])
@login_required
@require_perm("vehicle.delete")
def delete(vid):
    """Soft delete: archive the vehicle (keeps all its history; reversible)."""
    vehicle = _get_vehicle_or_404(vid)
    t = get_t()
    vehicle.is_active = False
    log_action("ARCHIVE", "vehicle", resource_id=vid, fleet_id=vehicle.fleet_id,
               detail=f"Archived vehicle '{vehicle.code}'")
    db.session.commit()
    flash("success|" + t.get("vehicle.archived", "Véhicule archivé."))
    return redirect(url_for("vehicles.index"))


@vehicles_bp.route("/vehicles/<int:vid>/reactivate", methods=["POST"])
@login_required
@require_perm("vehicle.delete")
def reactivate(vid):
    vehicle = _get_vehicle_or_404(vid)
    t = get_t()
    vehicle.is_active = True
    log_action("REACTIVATE", "vehicle", resource_id=vid, fleet_id=vehicle.fleet_id,
               detail=f"Reactivated vehicle '{vehicle.code}'")
    db.session.commit()
    flash("success|" + t.get("vehicle.reactivated", "Véhicule réactivé."))
    return redirect(url_for("vehicles.index"))
