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

from app import (current_user_fleet_ids, get_t, log_action, needs_approval,
                 require_perm, scoped, submit_change)
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

    q = scoped(Operator)
    if active_fleet:
        q = q.filter(Operator.fleet_id == active_fleet)
    operators = q.order_by(Operator.name).all()

    return render_template("operators.html", operators=operators,
                           filter_fleets=fleets, active_fleet=active_fleet)


@operators_bp.route("/operators/new", methods=["GET", "POST"])
@login_required
@require_perm("operator.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_operator_form(None)
        if error:
            flash("error|" + error)
        elif needs_approval("operator.create"):
            submit_change(resource_type="operator", action="create",
                          fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["operator.submitted"])
            return redirect(url_for("operators.index"))
        else:
            op = Operator(created_by=current_user.id, **data)
            db.session.add(op)
            db.session.flush()
            log_action("CREATE", "operator", resource_id=op.id, fleet_id=op.fleet_id,
                       detail=f"Created operator '{op.name}'")
            db.session.commit()
            flash("success|" + t["operator.created"])
            return redirect(url_for("operators.index"))
    return render_template("operator_form.html", operator=None, **_form_context(None))


@operators_bp.route("/operators/<int:oid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("operator.edit")
def edit(oid):
    op = _get_operator_or_404(oid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_operator_form(op)
        if error:
            flash("error|" + error)
        elif needs_approval("operator.edit", op.created_by, op.created_at):
            submit_change(resource_type="operator", action="update",
                          resource_id=op.id, fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["operator.submitted"])
            return redirect(url_for("operators.index"))
        else:
            for k, val in data.items():
                setattr(op, k, val)
            log_action("UPDATE", "operator", resource_id=op.id, fleet_id=op.fleet_id,
                       detail=f"Updated operator '{op.name}'")
            db.session.commit()
            flash("success|" + t["operator.updated"])
            return redirect(url_for("operators.index"))
    return render_template("operator_form.html", operator=op, **_form_context(op))


@operators_bp.route("/operators/<int:oid>/delete", methods=["POST"])
@login_required
@require_perm("operator.delete")
def delete(oid):
    op = _get_operator_or_404(oid)
    t = get_t()
    name, fleet_id = op.name, op.fleet_id
    db.session.delete(op)
    log_action("DELETE", "operator", resource_id=oid, fleet_id=fleet_id,
               detail=f"Deleted operator '{name}'")
    db.session.commit()
    flash("success|" + t["operator.deleted"])
    return redirect(url_for("operators.index"))
