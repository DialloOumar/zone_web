"""Vehicles blueprint — the operational keystone.

List / create / edit / detail / delete for vehicles, scoped to the user's
fleets. First module to wire the approval+grace flow: when a user's role
marks vehicle.* as requiring approval (and they're outside the grace window),
the change is parked in the approval queue instead of applied.
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required


import s3_storage
import vehicle_images
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, scoped, submit_change, with_current_fleet)
from models import (Alert, DailyEntry, Expense, Fleet, FuelMovement,
                    MaintenanceRecord, MaintenanceRule, Operator, StockMovement,
                    Vehicle, VehicleCategory, db)

vehicles_bp = Blueprint("vehicles", __name__)

PER_PAGE = 50   # rows on one screen


# Map an s3_storage error code to a localized message for the form.
_PHOTO_ERR_KEYS = {
    s3_storage.ERR_TOO_LARGE:     "photo.err.too_large",
    s3_storage.ERR_BAD_FORMAT:    "photo.err.bad_format",
    s3_storage.ERR_NOT_CONFIGURED: "photo.err.not_configured",
    s3_storage.ERR_S3:            "photo.err.s3",
    s3_storage.ERR_UNKNOWN:       "photo.err.unknown",
}


def _apply_photo_change(vehicle):
    """Handle the optional photo on a create/edit POST.

    A new upload replaces (and deletes) the previous object; ticking the
    "remove" box clears it. Returns a localized error string when an actually
    submitted photo can't be stored, else None. Never blocks the save on its
    own — the caller decides, but a bad/oversized file is worth reporting so
    the user retries. Assumes the vehicle row already has an id.
    """
    t = get_t()
    old_key = vehicle.photo_key
    if request.form.get("photo_remove") == "1":
        vehicle.photo_key = None
    upload = request.files.get("photo")
    if upload and upload.filename:
        key, err = s3_storage.upload_vehicle_photo(upload, vehicle.code)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Photo non enregistrée.")
        vehicle.photo_key = key
    # Delete the old object only once the DB will keep the new state.
    if old_key and old_key != vehicle.photo_key:
        s3_storage.delete_photo(old_key)
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    """Fleets the current user may file vehicles under (all, for super admin)."""
    fids = current_user_fleet_ids()
    # Archived fleets drop out of the pickers (no new data on a mothballed
    # fleet); existing data stays visible, scoped by current_user_fleet_ids.
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _get_vehicle_or_404(vid):
    v = db.session.get(Vehicle, vid)
    if not v or v.deleted_at is not None:
        abort(404)          # a deleted machine no longer exists for the UI
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

    # Code unique among live machines (case-insensitive), excluding self on
    # edit. A deleted machine doesn't count, so its code can be reused.
    q = Vehicle.query.filter(db.func.lower(Vehicle.code) == code.lower(),
                             Vehicle.deleted_at.is_(None))
    if vehicle:
        q = q.filter(Vehicle.id != vehicle.id)
    if q.first():
        return None, t["vehicle.err.code_taken"]

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
        "fleets": with_current_fleet(_accessible_fleets(), vehicle.fleet if vehicle else None),
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
    # A fleet of sixty scrolls forever; the code is what anyone types first.
    search = (request.args.get("q") or "").strip()
    if search:
        like = "%" + search + "%"
        q = q.filter(db.or_(Vehicle.code.ilike(like),
                            Vehicle.description.ilike(like)))
    pagination = q.order_by(Vehicle.code).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE,
        error_out=False)
    vehicles = pagination.items
    archived_count = scoped(Vehicle).filter(Vehicle.is_active.is_(False)).count()

    # Filter chips: only categories present in the user's accessible fleets.
    allowed = set()
    for f in _accessible_fleets():
        allowed.update(f.categories or [])
    filter_cats = [c for c in cats if c.code in allowed] or cats

    # Cards (photo-forward) vs table. An explicit ?view= wins and is remembered
    # in a cookie; otherwise fall back to the last choice, defaulting to cards.
    view = request.args.get("view")
    explicit = view in ("cards", "table")
    if not explicit:
        view = request.cookies.get("veh_view", "cards")
        if view not in ("cards", "table"):
            view = "cards"

    resp = make_response(render_template(
        "vehicles.html", vehicles=vehicles, pagination=pagination,
        view=view, search=search,
        filter_cats=filter_cats, active_code=active_code,
        show_archived=show_archived, archived_count=archived_count))
    if explicit:
        resp.set_cookie("veh_view", view, max_age=60 * 60 * 24 * 365,
                        samesite="Lax")
    return resp


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

    # Parts this machine took from the store. They are NOT ledger rows: the
    # money left when the parts were bought, and these movements only say the
    # cost was for this vehicle. Totals cover everything, the list shows the
    # last few.
    part_q = (StockMovement.query
              .join(MaintenanceRecord,
                    StockMovement.maintenance_record_id == MaintenanceRecord.id)
              .filter(MaintenanceRecord.vehicle_id == vid,
                      StockMovement.kind.in_(("sortie", "retour"))))
    all_parts = part_q.all()
    parts_total = (sum(m.value or 0 for m in all_parts if m.kind == "sortie")
                   - sum(m.value or 0 for m in all_parts if m.kind == "retour"))
    parts_used = (part_q.order_by(StockMovement.date.desc(),
                                  StockMovement.id.desc()).limit(10).all())
    # What the machine has cost so far: its own ledger rows plus its share of
    # the store. The two never overlap, so this can be added up safely.
    direct_total = sum(e.amount for e in
                       Expense.query.filter_by(vehicle_id=vid).all())

    return render_template("vehicle_detail.html", vehicle=vehicle,
                           entries=entries, expenses=expenses,
                           records=records, alerts=alerts,
                           parts_used=parts_used, parts_total=parts_total,
                           direct_total=direct_total)


def _vehicle_history(vid):
    """Rows that would be orphaned if the vehicle's row went away.

    Alerts are excluded on purpose: they are derived from the rules and get
    regenerated, so they never justify keeping a machine alive.
    """
    return (DailyEntry.query.filter_by(vehicle_id=vid).count()
            + FuelMovement.query.filter_by(vehicle_id=vid).count()
            + Expense.query.filter_by(vehicle_id=vid).count()
            + MaintenanceRecord.query.filter_by(vehicle_id=vid).count()
            + MaintenanceRule.query.filter_by(vehicle_id=vid).count())


def _render_vehicle_form(vehicle, error=None):
    """Render the vehicle form as a modal partial or a full page."""
    tpl = "_vehicle_form.html" if is_modal_request() else "vehicle_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, vehicle=vehicle, error=error,
                           **_form_context(vehicle)), status


@vehicles_bp.route("/vehicles/<int:vid>/image.svg")
@login_required
def image(vid):
    """The vehicle's category drawing with its own number written in, for a
    vehicle that has no photo.

    Drawn on each request rather than stored, so it never goes stale: rename
    the vehicle and the number follows, give it a photo and the photo wins,
    change the category's drawing and every vehicle without a photo follows.

    No vehicle.view check, deliberately. The thumbnail appears on the roster,
    on a citerne's page and in fuel movements, to people who may see those but
    not the vehicle page; the fleet scoping in _get_vehicle_or_404 is what keeps
    one fleet's machines from another's users.
    """
    v = _get_vehicle_or_404(vid)
    name = v.category.default_image if v.category else None
    body = vehicle_images.render_vehicle_drawing(name, v.code)
    if body is None:
        abort(404)
    resp = make_response(body)
    resp.headers["Content-Type"] = "image/svg+xml; charset=utf-8"
    # Opened on its own rather than in an <img>, an SVG is a document that
    # could run script. These carry none, and the policy makes sure of it.
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # Pages link here with ?v=<drawing_tag>, which changes with the type, the
    # drawing and the number, so the browser may keep a picture as long as it
    # likes: anything that would change it changes the address too.
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.set_etag(vehicle_images.drawing_tag(name, v.code))
    return resp.make_conditional(request)


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
        perr = _apply_photo_change(v)
        if perr:
            db.session.rollback()
            return _render_vehicle_form(None, perr)
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
        perr = _apply_photo_change(vehicle)
        if perr:
            db.session.rollback()
            return _render_vehicle_form(vehicle, perr)
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


@vehicles_bp.route("/vehicles/<int:vid>/destroy", methods=["POST"])
@login_required
@require_perm("vehicle.delete")
def destroy(vid):
    """Delete an already-archived machine. Must be archived first.

    A machine that logged something keeps its row as a tombstone: it vanishes
    from every UI and frees its code, but the entries, fuel movements and
    expenses hanging off it would otherwise be orphaned.

    A machine that logged nothing has no such history to protect, so the row
    really goes. That matters beyond tidiness: a tombstone keeps holding its
    foreign keys, so a never-used machine left behind would pin its fleet and
    its category forever, neither of which could then be deleted.
    """
    vehicle = _get_vehicle_or_404(vid)
    t = get_t()
    if vehicle.is_active:
        flash("error|" + t.get("delete.archive_first",
                               "Archivez d'abord, puis supprimez."))
        return redirect(url_for("vehicles.index"))

    fleet_id, code = vehicle.fleet_id, vehicle.code
    if _vehicle_history(vid):
        vehicle.deleted_at = datetime.utcnow()
        detail = f"Deleted vehicle '{code}' (kept for history)"
    else:
        Alert.query.filter_by(vehicle_id=vid).delete(synchronize_session=False)
        db.session.delete(vehicle)
        detail = f"Deleted vehicle '{code}' (no history, row removed)"
    log_action("DELETE", "vehicle", resource_id=vid, fleet_id=fleet_id, detail=detail)
    db.session.commit()
    flash("success|" + t.get("vehicle.deleted", "Véhicule supprimé."))
    return redirect(url_for("vehicles.index", archived=1))
