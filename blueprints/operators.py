"""Operators blueprint — the driver pool.

List / create / edit / delete drivers, scoped to the user's fleets. Operators
feed the driver dropdowns on the vehicle and daily-entry forms. DailyEntry
stores the operator as a string snapshot, so deleting an operator never
orphans historical data — no delete guard needed.

Reuses the same approval+grace flow as vehicles via submit_change().
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, scoped, submit_change, with_current_fleet)
from models import (DailyEntry, Expense, Fleet, FuelMovement,
                    MaintenanceRecord, Operator, Vehicle, db)

operators_bp = Blueprint("operators", __name__)


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


def _read_operator_form(operator):
    """Validate the form into a column dict. Returns (data, None) or (None, err)."""
    t = get_t()
    name = (request.form.get("name") or "").strip()
    fleet_id = request.form.get("fleet_id", type=int)

    if not name:
        return None, t["operator.err.name_required"]
    if not fleet_id:
        return None, t["operator.err.fleet_required"]

    fids = current_user_fleet_ids()
    if fids is not None and fleet_id not in fids:
        return None, t["error.forbidden"]
    if not db.session.get(Fleet, fleet_id):
        return None, t["operator.err.fleet_required"]

    # Name unique within the fleet (matches the DB constraint), excluding self.
    q = Operator.query.filter(Operator.fleet_id == fleet_id,
                              db.func.lower(Operator.name) == name.lower())
    if operator:
        q = q.filter(Operator.id != operator.id)
    if q.first():
        return None, t["operator.err.name_taken"]

    data = dict(
        name=name,
        fleet_id=fleet_id,
        phone=(request.form.get("phone") or "").strip() or None,
        license_number=(request.form.get("license_number") or "").strip() or None,
        notes=(request.form.get("notes") or "").strip() or None,
        is_active=request.form.get("is_active") is not None,
    )
    return data, None


def _form_context(operator):
    return {
        "fleets": with_current_fleet(_accessible_fleets(), operator.fleet if operator else None),
        "form_active": (request.form.get("is_active") is not None)
        if request.method == "POST"
        else (operator.is_active if operator else True),
    }


# ── Routes ───────────────────────────────────────────────────────────────────


def _operator_usage(op):
    """What still refers to a driver, and whether the row can be deleted.

    Entries, services, fuel movements and expenses store the driver as a name,
    not an id — deliberately, so history survives a rename. Deleting the row
    therefore orphans nothing, but it would strip that history of the only
    place the name is still a real person with a phone number and a licence.
    So anything logged under the name blocks, and the driver gets archived
    instead.

    Being a vehicle's default driver is the one genuine foreign key, and it
    blocks too: silently clearing someone else's default is a surprise, and
    the fix is one edit away on the vehicle.
    """
    name, fid = op.name, op.fleet_id
    usage = {
        "entries": (DailyEntry.query
                    .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
                    .filter(Vehicle.fleet_id == fid, DailyEntry.operator == name).count()),
        "records": (MaintenanceRecord.query
                    .join(Vehicle, MaintenanceRecord.vehicle_id == Vehicle.id)
                    .filter(Vehicle.fleet_id == fid,
                            MaintenanceRecord.operator == name).count()),
        # A fill or a cost can sit outside any vehicle, so there is no fleet to
        # join through; matching the name alone over-counts at worst, and an
        # over-count only ever means "archive it instead", never a bad delete.
        "movements": FuelMovement.query.filter_by(operator=name).count(),
        "expenses": Expense.query.filter_by(operator=name).count(),
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
    fleets = _accessible_fleets()
    active_fleet = request.args.get("fleet", type=int)
    show_archived = request.args.get("archived") == "1"

    q = scoped(Operator).filter(Operator.is_active.is_(not show_archived))
    if active_fleet:
        q = q.filter(Operator.fleet_id == active_fleet)
    operators = q.order_by(Operator.name).all()
    archived_count = scoped(Operator).filter(Operator.is_active.is_(False)).count()
    usage = {o.id: _operator_usage(o) for o in operators}

    return render_template("operators.html", operators=operators, usage=usage,
                           filter_fleets=fleets, active_fleet=active_fleet,
                           show_archived=show_archived, archived_count=archived_count)


@operators_bp.route("/operators/<int:oid>")
@login_required
@require_perm("operator.view")
def detail(oid):
    """Driver sheet — activity KPIs for a month, vehicles driven, and the
    services he's tied to. Daily entries / records store the driver as a name,
    so we match by name within the operator's fleet (names are unique there)."""
    op = _get_operator_or_404(oid)
    name, fid = op.name, op.fleet_id

    month = request.args.get("month", "")
    try:
        datetime.strptime(month, "%Y-%m")
    except (TypeError, ValueError):
        month = datetime.utcnow().strftime("%Y-%m")

    def _scoped_entries():
        return (DailyEntry.query.join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
                .filter(Vehicle.fleet_id == fid, DailyEntry.operator == name))

    # Activity KPIs for the selected month.
    co = func.coalesce
    n_pointages, tot_trips, tot_hours, tot_km = (
        db.session.query(
            func.count(DailyEntry.id),
            co(func.sum(DailyEntry.trips), 0),
            co(func.sum(DailyEntry.hours), 0.0),
            co(func.sum(DailyEntry.kilometers), 0.0))
        .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
        .filter(Vehicle.fleet_id == fid, DailyEntry.operator == name,
                DailyEntry.date.like(month + "%")).one())
    last_date = (db.session.query(func.max(DailyEntry.date))
                 .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
                 .filter(Vehicle.fleet_id == fid, DailyEntry.operator == name).scalar())

    # Vehicles driven (all-time): default-driver assignments ∪ vehicles pointed.
    default_ids = {v.id for v in Vehicle.query.filter_by(default_operator_id=op.id).all()}
    pointed_ids = {r[0] for r in _scoped_entries()
                   .with_entities(DailyEntry.vehicle_id).distinct()}
    veh_ids = default_ids | pointed_ids
    vehicles = (Vehicle.query.filter(Vehicle.id.in_(veh_ids)).order_by(Vehicle.code).all()
                if veh_ids else [])

    # Services the driver is tied to.
    records = (MaintenanceRecord.query
               .join(Vehicle, MaintenanceRecord.vehicle_id == Vehicle.id)
               .filter(Vehicle.fleet_id == fid, MaintenanceRecord.operator == name)
               .order_by(MaintenanceRecord.date.desc()).limit(50).all())

    # Costs attributed to this driver — stacked by category over the 6 months
    # ending at the selected month (Expense.operator is the driver tag). Fuel and
    # services are ordinary ledger categories, so one query covers them all.
    from blueprints.expenses import ALL_CATEGORIES
    t = get_t()
    y, mo = int(month[:4]), int(month[5:7])
    months = []
    for i in range(5, -1, -1):
        mm, yy = mo - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append("%04d-%02d" % (yy, mm))
    series = {c: [0] * len(months) for c in ALL_CATEGORIES}
    idx = {m: i for i, m in enumerate(months)}
    ym = func.substr(Expense.date, 1, 7)
    rows = (db.session.query(ym, Expense.category, func.sum(Expense.amount))
            .filter(Expense.fleet_id == fid, Expense.operator == name, ym.in_(months))
            .group_by(ym, Expense.category).all())
    for m, cat, amt in rows:
        if cat in series and m in idx:
            series[cat][idx[m]] = int(amt or 0)
    chart_series = [{"cat": c, "label": t["expense.cat." + c], "data": series[c]}
                    for c in ALL_CATEGORIES if any(series[c])]

    expense_chart = {"months": months, "series": chart_series}

    return render_template(
        "operator_detail.html", op=op, month=month,
        n_pointages=n_pointages, tot_trips=int(tot_trips or 0),
        tot_hours=float(tot_hours or 0), tot_km=float(tot_km or 0),
        last_date=last_date, vehicles=vehicles, default_ids=default_ids,
        records=records, expense_chart=expense_chart)


def _render_operator_form(operator, error=None):
    """Render the operator form as a modal partial or a full page."""
    tpl = "_operator_form.html" if is_modal_request() else "operator_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, operator=operator, error=error,
                           **_form_context(operator)), status


@operators_bp.route("/operators/new", methods=["GET", "POST"])
@login_required
@require_perm("operator.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_operator_form(None)
        if error:
            return _render_operator_form(None, error)
        if needs_approval("operator.create"):
            submit_change(resource_type="operator", action="create",
                          fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["operator.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("operators.index"))
        op = Operator(created_by=current_user.id, **data)
        db.session.add(op)
        db.session.flush()
        log_action("CREATE", "operator", resource_id=op.id, fleet_id=op.fleet_id,
                   detail=f"Created operator '{op.name}'")
        db.session.commit()
        flash("success|" + t["operator.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("operators.index"))
    return _render_operator_form(None)


@operators_bp.route("/operators/<int:oid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("operator.edit")
def edit(oid):
    op = _get_operator_or_404(oid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_operator_form(op)
        if error:
            return _render_operator_form(op, error)
        if needs_approval("operator.edit", op.created_by, op.created_at):
            submit_change(resource_type="operator", action="update",
                          resource_id=op.id, fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["operator.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("operators.index"))
        for k, val in data.items():
            setattr(op, k, val)
        log_action("UPDATE", "operator", resource_id=op.id, fleet_id=op.fleet_id,
                   detail=f"Updated operator '{op.name}'")
        db.session.commit()
        flash("success|" + t["operator.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("operators.index"))
    return _render_operator_form(op)



@operators_bp.route("/operators/<int:oid>/delete", methods=["POST"])
@login_required
@require_perm("operator.delete")
def delete(oid):
    """Soft delete: archive the operator (preserves history; reversible)."""
    op = _get_operator_or_404(oid)
    t = get_t()
    op.is_active = False
    log_action("ARCHIVE", "operator", resource_id=oid, fleet_id=op.fleet_id,
               detail=f"Archived operator '{op.name}'")
    db.session.commit()
    flash("success|" + t.get("operator.archived", "Conducteur archivé."))
    return redirect(url_for("operators.index"))


@operators_bp.route("/operators/<int:oid>/destroy", methods=["POST"])
@login_required
@require_perm("operator.delete")
def destroy(oid):
    """Hard delete, allowed only while nothing was ever logged under the name.

    Re-checked here rather than trusting the button, since the list may have
    been rendered before someone pointed a day of work at this driver.
    """
    op = _get_operator_or_404(oid)
    t = get_t()
    usage = _operator_usage(op)
    if not usage["deletable"]:
        flash("error|" + t.get("operator.err.delete_blocked",
                               "Impossible de supprimer : ce conducteur a déjà "
                               "de l'activité enregistrée."))
        return redirect(url_for("operators.index"))

    name, fid = op.name, op.fleet_id
    # A tombstoned machine still names him as its default driver. That does not
    # block the delete — nobody can open a deleted machine to unassign him —
    # but the key is real, so unhook it or the delete is refused outright.
    ghosts = (Vehicle.query
              .filter_by(default_operator_id=op.id)
              .filter(Vehicle.deleted_at.isnot(None))
              .update({Vehicle.default_operator_id: None}, synchronize_session=False))
    db.session.delete(op)
    log_action("DELETE", "operator", resource_id=oid, fleet_id=fid,
               detail="Deleted operator '%s' (no activity, row removed)%s"
                      % (name, " + unhooked from %d deleted vehicle(s)" % ghosts
                         if ghosts else ""))
    db.session.commit()
    flash("success|" + t.get("operator.deleted", "Conducteur supprimé."))
    return redirect(url_for("operators.index"))


@operators_bp.route("/operators/<int:oid>/reactivate", methods=["POST"])
@login_required
@require_perm("operator.delete")
def reactivate(oid):
    op = _get_operator_or_404(oid)
    t = get_t()
    op.is_active = True
    log_action("REACTIVATE", "operator", resource_id=oid, fleet_id=op.fleet_id,
               detail=f"Reactivated operator '{op.name}'")
    db.session.commit()
    flash("success|" + t.get("operator.reactivated", "Conducteur réactivé."))
    return redirect(url_for("operators.index"))
