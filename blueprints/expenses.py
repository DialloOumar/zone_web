"""Expenses blueprint — all cost-bearing events.

Fuel (moved out of daily entries), maintenance and any other vehicle/fleet
cost. An expense always belongs to a fleet (for scoping) and optionally to a
vehicle (null = a fleet-wide cost like insurance). Reuses the shared
permission, approval/grace, audit and modal machinery.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, scoped, submit_change)
from models import Expense, Fleet, Vehicle, db

expenses_bp = Blueprint("expenses", __name__)

# Fixed catalogue of expense categories (codes; labels via expense.cat.<code>).
# Maintenance costs are NOT here — they live on the maintenance service record
# (which has its own cost field) so a service is never counted twice.
EXPENSE_CATEGORIES = [
    "fuel", "assurance", "accident", "lavage", "agent", "autre",
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    fids = current_user_fleet_ids()
    q = Fleet.query.order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _accessible_vehicles():
    q = Vehicle.query
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _get_expense_or_404(xid):
    x = db.session.get(Expense, xid)
    if not x:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and x.fleet_id not in fids:
        abort(403)
    return x


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _read_expense_form(expense):
    """Validate the form into a column dict. Returns (data, None) or (None, err)."""
    t = get_t()
    category = (request.form.get("category") or "").strip()
    fleet_id = request.form.get("fleet_id", type=int)
    vehicle_id = request.form.get("vehicle_id", type=int) or None
    date_str = (request.form.get("date") or "").strip()

    if category not in EXPENSE_CATEGORIES:
        return None, t["expense.err.category_required"]
    if not fleet_id:
        return None, t["expense.err.fleet_required"]

    fids = current_user_fleet_ids()
    if fids is not None and fleet_id not in fids:
        return None, t["error.forbidden"]
    if not db.session.get(Fleet, fleet_id):
        return None, t["expense.err.fleet_required"]

    # If a vehicle is set it must belong to the chosen fleet.
    if vehicle_id:
        v = db.session.get(Vehicle, vehicle_id)
        if not v or v.fleet_id != fleet_id:
            return None, t["expense.err.vehicle_fleet"]

    if not date_str or not _valid_date(date_str):
        return None, t["expense.err.date_required"]

    raw = (request.form.get("amount") or "").strip().replace(" ", "").replace(",", "")
    try:
        amount = int(round(float(raw)))
    except (TypeError, ValueError):
        return None, t["expense.err.amount_required"]
    if amount <= 0:
        return None, t["expense.err.amount_required"]

    liters = None
    if category == "fuel":
        lr = (request.form.get("liters") or "").strip().replace(",", ".")
        if lr:
            try:
                liters = float(lr)
            except ValueError:
                return None, t["expense.err.bad_number"]

    data = dict(
        category=category,
        fleet_id=fleet_id,
        vehicle_id=vehicle_id,
        date=date_str,
        amount=amount,
        liters=liters,
        currency="GNF",
        supplier=(request.form.get("supplier") or "").strip() or None,
        description=(request.form.get("description") or "").strip() or None,
    )
    return data, None


def _form_context(expense):
    preset_vehicle = request.args.get("vehicle_id", type=int)
    preset_fleet = None
    if preset_vehicle:
        v = db.session.get(Vehicle, preset_vehicle)
        if v:
            preset_fleet = v.fleet_id
    return {
        "fleets": _accessible_fleets(),
        "vehicles": _accessible_vehicles(),
        "categories": EXPENSE_CATEGORIES,
        "preset_vehicle": preset_vehicle,
        "preset_fleet": preset_fleet,
        "today": date.today().isoformat(),
    }


def _render_expense_form(expense, error=None):
    tpl = "_expense_form.html" if is_modal_request() else "expense_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, expense=expense, error=error, **_form_context(expense)), status


# ── Routes ───────────────────────────────────────────────────────────────────


@expenses_bp.route("/expenses")
@login_required
@require_perm("expense.view")
def index():
    active_cat = request.args.get("category") or ""
    q = scoped(Expense)
    if active_cat in EXPENSE_CATEGORIES:
        q = q.filter(Expense.category == active_cat)
    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).limit(300).all()
    total = sum(e.amount for e in expenses)
    return render_template("expenses.html", expenses=expenses,
                           categories=EXPENSE_CATEGORIES, active_cat=active_cat,
                           total=total)


@expenses_bp.route("/expenses/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_expense_form(None)
        if error:
            return _render_expense_form(None, error)
        if needs_approval("expense.create"):
            submit_change(resource_type="expense", action="create",
                          fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["expense.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
        x = Expense(created_by=current_user.id, **data)
        db.session.add(x)
        db.session.flush()
        log_action("CREATE", "expense", resource_id=x.id, fleet_id=x.fleet_id,
                   detail=f"Logged {x.category} expense {x.amount} GNF")
        db.session.commit()
        flash("success|" + t["expense.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
    return _render_expense_form(None)


@expenses_bp.route("/expenses/<int:xid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("expense.edit")
def edit(xid):
    expense = _get_expense_or_404(xid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_expense_form(expense)
        if error:
            return _render_expense_form(expense, error)
        if needs_approval("expense.edit", expense.created_by, expense.created_at):
            submit_change(resource_type="expense", action="update",
                          resource_id=expense.id, fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["expense.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
        for k, val in data.items():
            setattr(expense, k, val)
        log_action("UPDATE", "expense", resource_id=expense.id, fleet_id=expense.fleet_id,
                   detail=f"Edited expense #{expense.id}")
        db.session.commit()
        flash("success|" + t["expense.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
    return _render_expense_form(expense)


@expenses_bp.route("/expenses/<int:xid>/delete", methods=["POST"])
@login_required
@require_perm("expense.delete")
def delete(xid):
    expense = _get_expense_or_404(xid)
    t = get_t()
    fleet_id = expense.fleet_id
    db.session.delete(expense)
    log_action("DELETE", "expense", resource_id=xid, fleet_id=fleet_id,
               detail=f"Deleted expense #{xid}")
    db.session.commit()
    flash("success|" + t["expense.deleted"])
    return redirect(request.referrer or url_for("expenses.index"))
