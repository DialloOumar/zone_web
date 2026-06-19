"""Operators blueprint — the driver pool.

List / create / edit / delete drivers, scoped to the user's fleets. Operators
feed the driver dropdowns on the vehicle and daily-entry forms. DailyEntry
stores the operator as a string snapshot, so deleting an operator never
orphans historical data — no delete guard needed.

Reuses the same approval+grace flow as vehicles via submit_change().
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, scoped, submit_change)
from models import Fleet, Operator, db

operators_bp = Blueprint("operators", __name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    fids = current_user_fleet_ids()
    q = Fleet.query.order_by(Fleet.name)
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
        "fleets": _accessible_fleets(),
        "form_active": (request.form.get("is_active") is not None)
        if request.method == "POST"
        else (operator.is_active if operator else True),
    }


# ── Routes ───────────────────────────────────────────────────────────────────


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

    return render_template("operators.html", operators=operators,
                           filter_fleets=fleets, active_fleet=active_fleet,
                           show_archived=show_archived, archived_count=archived_count)


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
