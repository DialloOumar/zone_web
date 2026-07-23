"""Expenses blueprint — the money ledger and its two entry screens.

Every cost lives in one table (Expense), but is captured where it belongs:

  • /carburant — fuel, always tied to a vehicle, with litres
  • /expenses  — accident / lavage / autre, tied to a vehicle or not

Maintenance costs also land in the ledger (category "entretien") but are written
by the maintenance blueprint alongside their service record, so they show here
read-only. A cost with no vehicle carries a `label`; a cost with no fleet is a
company cost, visible to anyone allowed on the expenses page.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, submit_change)
from models import Expense, Fleet, Operator, Vehicle, db

expenses_bp = Blueprint("expenses", __name__)

# Category codes (labels via expense.cat.<code>).
FUEL_CATEGORY = "fuel"           # own screen: /carburant
MAINTENANCE_CATEGORY = "entretien"  # written by the maintenance blueprint only

# What the Dépenses form offers. Fuel has its own screen and "entretien" is
# created automatically from a service record — neither is selectable here, so
# the same cost can never be entered twice.
EXPENSE_CATEGORIES = ["accident", "lavage", "autre"]

# Every category that can appear in the ledger (filters, labels).
ALL_CATEGORIES = [FUEL_CATEGORY] + EXPENSE_CATEGORIES + [MAINTENANCE_CATEGORY]

PAYMENT_METHODS = ["mobile_money", "cash", "transfer", "cheque", "other"]


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


def _accessible_operators():
    q = Operator.query.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Operator.fleet_id.in_(fids))
    return q.order_by(Operator.name).all()


def _scoped_expenses():
    """Ledger rows the user may see: the costs of their own fleets, plus the
    company costs (no fleet), which anyone allowed on this page can see."""
    q = Expense.query
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(db.or_(Expense.fleet_id.in_(fids), Expense.fleet_id.is_(None)))
    return q


def _get_expense_or_404(xid):
    x = db.session.get(Expense, xid)
    if not x:
        abort(404)
    fids = current_user_fleet_ids()
    # A company cost (no fleet) is readable by anyone who may see expenses.
    if fids is not None and x.fleet_id is not None and x.fleet_id not in fids:
        abort(403)
    return x


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _common_fields(t):
    """Date, amount and payment fields shared by both screens.
    Returns (dict, None) or (None, error)."""
    date_str = (request.form.get("date") or "").strip()
    if not date_str or not _valid_date(date_str):
        return None, t["expense.err.date_required"]

    raw = (request.form.get("amount") or "").strip().replace(" ", "").replace(",", "")
    try:
        amount = int(round(float(raw)))
    except (TypeError, ValueError):
        return None, t["expense.err.amount_required"]
    if amount <= 0:
        return None, t["expense.err.amount_required"]

    method = (request.form.get("payment_method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, t["expense.err.payment_required"]

    return dict(
        date=date_str,
        amount=amount,
        currency="GNF",
        payment_method=method,
        payment_reference=(request.form.get("payment_reference") or "").strip() or None,
        supplier=(request.form.get("supplier") or "").strip() or None,
        description=(request.form.get("description") or "").strip() or None,
    ), None


def _read_expense_form(expense):
    """Dépenses form: a vehicle cost, or a free-standing one named by `label`.
    Returns (data, None) or (None, err)."""
    t = get_t()
    common, error = _common_fields(t)
    if error:
        return None, error

    category = (request.form.get("category") or "").strip()
    if category not in EXPENSE_CATEGORIES:
        return None, t["expense.err.category_required"]

    fids = current_user_fleet_ids()
    is_vehicle = request.form.get("is_vehicle") is not None

    if is_vehicle:
        vehicle_id = request.form.get("vehicle_id", type=int) or None
        if not vehicle_id:
            return None, t["expense.err.vehicle_required"]
        v = db.session.get(Vehicle, vehicle_id)
        if not v:
            return None, t["expense.err.vehicle_required"]
        if fids is not None and v.fleet_id not in fids:
            return None, t["error.forbidden"]
        # The fleet always follows the vehicle — never asked for separately.
        common.update(vehicle_id=v.id, fleet_id=v.fleet_id, label=None,
                      operator=(request.form.get("operator") or "").strip() or None)
    else:
        label = (request.form.get("label") or "").strip()
        if not label:
            return None, t["expense.err.label_required"]
        # Fleet is optional here: none at all = a company cost.
        fleet_id = request.form.get("fleet_id", type=int) or None
        if fleet_id:
            if fids is not None and fleet_id not in fids:
                return None, t["error.forbidden"]
            if not db.session.get(Fleet, fleet_id):
                return None, t["expense.err.fleet_required"]
        common.update(vehicle_id=None, fleet_id=fleet_id, label=label, operator=None)

    common.update(category=category, liters=None)
    return common, None


def _read_fuel_form(expense):
    """Carburant form: always a vehicle, always category "fuel", with litres."""
    t = get_t()
    common, error = _common_fields(t)
    if error:
        return None, error

    vehicle_id = request.form.get("vehicle_id", type=int) or None
    if not vehicle_id:
        return None, t["expense.err.vehicle_required"]
    v = db.session.get(Vehicle, vehicle_id)
    if not v:
        return None, t["expense.err.vehicle_required"]
    fids = current_user_fleet_ids()
    if fids is not None and v.fleet_id not in fids:
        return None, t["error.forbidden"]

    liters = None
    lr = (request.form.get("liters") or "").strip().replace(",", ".")
    if lr:
        try:
            liters = float(lr)
        except ValueError:
            return None, t["expense.err.bad_number"]
        if liters <= 0:
            return None, t["expense.err.bad_number"]

    common.update(
        category=FUEL_CATEGORY, vehicle_id=v.id, fleet_id=v.fleet_id,
        label=None, liters=liters,
        operator=(request.form.get("operator") or "").strip() or None,
    )
    return common, None


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
        "operators": _accessible_operators(),
        "categories": EXPENSE_CATEGORIES,
        "payment_methods": PAYMENT_METHODS,
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
    """Everything except fuel, which has its own screen."""
    active = request.args.get("f") or ""
    q = _scoped_expenses().filter(Expense.category != FUEL_CATEGORY)
    if active == "general":
        q = q.filter(Expense.vehicle_id.is_(None))
    elif active in EXPENSE_CATEGORIES + [MAINTENANCE_CATEGORY]:
        q = q.filter(Expense.category == active)
    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).limit(300).all()
    total = sum(e.amount for e in expenses)
    return render_template(
        "expenses.html", expenses=expenses, total=total, active=active,
        filters=["general"] + EXPENSE_CATEGORIES + [MAINTENANCE_CATEGORY],
        maintenance_category=MAINTENANCE_CATEGORY)


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
    if expense.maintenance_record_id:
        # Owned by its service record — edited there, so the two can't drift.
        flash("error|" + t["expense.err.maintenance_locked"])
        return redirect(url_for("expenses.index"))
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
    if expense.maintenance_record_id:
        flash("error|" + t["expense.err.maintenance_locked"])
        return redirect(request.referrer or url_for("expenses.index"))
    fleet_id = expense.fleet_id
    db.session.delete(expense)
    log_action("DELETE", "expense", resource_id=xid, fleet_id=fleet_id,
               detail=f"Deleted expense #{xid}")
    db.session.commit()
    flash("success|" + t["expense.deleted"])
    return redirect(request.referrer or url_for("expenses.index"))


# ── Carburant ────────────────────────────────────────────────────────────────


def _render_fuel_form(expense, error=None):
    tpl = "_fuel_form.html" if is_modal_request() else "fuel_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, expense=expense, error=error, **_form_context(expense)), status


@expenses_bp.route("/carburant")
@login_required
@require_perm("expense.view")
def fuel_index():
    rows = (_scoped_expenses().filter(Expense.category == FUEL_CATEGORY)
            .order_by(Expense.date.desc(), Expense.id.desc()).limit(300).all())
    total = sum(e.amount for e in rows)
    total_liters = sum(e.liters or 0 for e in rows)
    return render_template("fuel.html", expenses=rows, total=total,
                           total_liters=total_liters)


@expenses_bp.route("/carburant/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def fuel_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_fuel_form(None)
        if error:
            return _render_fuel_form(None, error)
        if needs_approval("expense.create"):
            submit_change(resource_type="expense", action="create",
                          fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["expense.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("expenses.fuel_index"))
        x = Expense(created_by=current_user.id, **data)
        db.session.add(x)
        db.session.flush()
        log_action("CREATE", "expense", resource_id=x.id, fleet_id=x.fleet_id,
                   detail=f"Logged fuel {x.amount} GNF")
        db.session.commit()
        flash("success|" + t["expense.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.fuel_index"))
    return _render_fuel_form(None)


@expenses_bp.route("/carburant/<int:xid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("expense.edit")
def fuel_edit(xid):
    expense = _get_expense_or_404(xid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_fuel_form(expense)
        if error:
            return _render_fuel_form(expense, error)
        if needs_approval("expense.edit", expense.created_by, expense.created_at):
            submit_change(resource_type="expense", action="update",
                          resource_id=expense.id, fleet_id=data["fleet_id"], payload=data)
            flash("success|" + t["expense.submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("expenses.fuel_index"))
        for k, val in data.items():
            setattr(expense, k, val)
        log_action("UPDATE", "expense", resource_id=expense.id, fleet_id=expense.fleet_id,
                   detail=f"Edited fuel expense #{expense.id}")
        db.session.commit()
        flash("success|" + t["expense.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.fuel_index"))
    return _render_fuel_form(expense)


@expenses_bp.route("/carburant/<int:xid>/delete", methods=["POST"])
@login_required
@require_perm("expense.delete")
def fuel_delete(xid):
    expense = _get_expense_or_404(xid)
    t = get_t()
    fleet_id = expense.fleet_id
    db.session.delete(expense)
    log_action("DELETE", "expense", resource_id=xid, fleet_id=fleet_id,
               detail=f"Deleted fuel expense #{xid}")
    db.session.commit()
    flash("success|" + t["expense.deleted"])
    return redirect(request.referrer or url_for("expenses.fuel_index"))
