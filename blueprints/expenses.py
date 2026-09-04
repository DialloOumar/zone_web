"""Expenses blueprint — the money ledger and its two entry screens.

Every cost lives in one table (Expense), but is captured where it belongs:

  • /carburant — fuel, always tied to a vehicle, with litres
  • /expenses  — a named cost, on a fleet or on the company, no vehicle

Maintenance costs also land in the ledger (category "entretien") but are written
by the maintenance blueprint alongside their service record, so they show here
read-only. A cost with no vehicle carries a `label`; a cost with no fleet is a
company cost, visible to anyone allowed on the expenses page.
"""
import calendar
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, submit_change, with_current_fleet)
from models import CashMovement, Expense, Fleet, Operator, Vehicle, db

expenses_bp = Blueprint("expenses", __name__)

# Category codes (labels via expense.cat.<code>).
FUEL_CATEGORY = "fuel"           # own screen: /carburant
MAINTENANCE_CATEGORY = "entretien"  # written by the maintenance blueprint only
PARTS_CATEGORY = "pieces"        # written by the stock blueprint only (a receipt)

# Categories that manual costs used to be filed under. Nothing is filed by hand
# any more — the Dépenses form asks for a label instead, which says far more
# than a fixed list ever did — but old rows keep theirs, so the filters and the
# labels stay. New ones all land in DEFAULT_CATEGORY.
EXPENSE_CATEGORIES = ["accident", "lavage", "autre"]
DEFAULT_CATEGORY = "autre"

# Every category that can appear in the ledger (filters, labels).
ALL_CATEGORIES = ([FUEL_CATEGORY] + EXPENSE_CATEGORIES
                  + [MAINTENANCE_CATEGORY, PARTS_CATEGORY])

PAYMENT_METHODS = ["mobile_money", "cash", "transfer", "cheque", "other"]

# The Dépenses page is the cash box: money is handed over, and spent out of it.
# Only the two ways that float actually moves are offered there. Older rows keep
# whatever they were saved with and still read fine.
CASH_METHODS = ["cash", "mobile_money"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _accessible_fleets():
    fids = current_user_fleet_ids()
    # Archived fleets drop out of the pickers (no new data on a mothballed
    # fleet); existing data stays visible, scoped by current_user_fleet_ids.
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _accessible_vehicles():
    # Entry picker: only vehicles on a live fleet — no new expense against a
    # mothballed fleet. History stays reachable through the scoped list views.
    q = Vehicle.query.filter(Vehicle.fleet.has(Fleet.is_active.is_(True)))
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


def _common_fields(t, methods=None):
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
    if method not in (methods or PAYMENT_METHODS):
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
    """Dépenses form: a cost named by `label`, on a fleet or on the company.

    Neither a category, a vehicle nor a supplier is asked for here. The label is
    what says what the cost is, which beats picking from a fixed list. Costs
    that belong to a machine are recorded on its service record, not here.

    Returns (data, None) or (None, err).
    """
    t = get_t()
    common, error = _common_fields(t, CASH_METHODS)
    if error:
        return None, error

    fids = current_user_fleet_ids()

    label = (request.form.get("label") or "").strip()
    if not label:
        return None, t["expense.err.label_required"]

    # Fleet is optional: none at all = a company cost.
    fleet_id = request.form.get("fleet_id", type=int) or None
    if fleet_id:
        if fids is not None and fleet_id not in fids:
            return None, t["error.forbidden"]
        if not db.session.get(Fleet, fleet_id):
            return None, t["expense.err.fleet_required"]

    # Optional: how many of whatever this paid for. Blank stays blank rather
    # than becoming a zero nobody typed.
    raw_qty = (request.form.get("quantity") or "").strip().replace(",", ".")
    quantity = None
    if raw_qty:
        try:
            quantity = float(raw_qty)
        except ValueError:
            return None, t.get("expense.err.quantity", "Quantité invalide.")
        if quantity <= 0:
            return None, t.get("expense.err.quantity", "Quantité invalide.")

    common.update(vehicle_id=None, fleet_id=fleet_id, label=label,
                  operator=None, supplier=None, category=DEFAULT_CATEGORY,
                  liters=None, quantity=quantity)
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
    return {
        "fleets": with_current_fleet(_accessible_fleets(), expense.fleet if expense else None),
        "payment_methods": CASH_METHODS,
        "today": date.today().isoformat(),
    }


def _fuel_form_context(expense):
    """The Carburant form still picks a machine and its driver — a fill-up is
    always a vehicle's. Only the Dépenses form dropped those."""
    return {
        "vehicles": _accessible_vehicles(),
        "operators": _accessible_operators(),
        "payment_methods": PAYMENT_METHODS,
        "preset_vehicle": request.args.get("vehicle_id", type=int),
        "today": date.today().isoformat(),
    }


def _render_expense_form(expense, error=None):
    tpl = "_expense_form.html" if is_modal_request() else "expense_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, expense=expense, error=error, **_form_context(expense)), status


# ── Routes ───────────────────────────────────────────────────────────────────


def _cash_expenses():
    """The costs that come out of the cash box: the ones entered on this page.
    A service's cost and a stock receipt are in the ledger too, but they are not
    paid out of this float, so they never touch its balance."""
    return _scoped_expenses().filter(Expense.category.notin_(
        [FUEL_CATEGORY, MAINTENANCE_CATEGORY, PARTS_CATEGORY]))


def cash_balance():
    """What is left in the box: everything paid in, minus everything spent from
    it. Not bounded by the month on screen — a balance carries over."""
    paid_in = db.session.query(db.func.coalesce(
        db.func.sum(CashMovement.amount), 0)).scalar() or 0
    spent = db.session.query(db.func.coalesce(
        db.func.sum(Expense.amount), 0)).filter(
        Expense.category.notin_([FUEL_CATEGORY, MAINTENANCE_CATEGORY,
                                 PARTS_CATEGORY])).scalar() or 0
    return paid_in - spent


def _period_bounds():
    """The window the page and the report both read: one from/to, nothing else.

    Landing on the page with nothing asked for shows the current month, which
    is what someone opening a cash book wants to see. Clearing either end opens
    that side — "everything since March" is a normal thing to ask for — and a
    cleared field stays cleared rather than snapping back to the month.
    """
    today = date.today()
    first = today.replace(day=1).isoformat()
    last = today.replace(
        day=calendar.monthrange(today.year, today.month)[1]).isoformat()
    if "date_from" in request.args or "date_to" in request.args:
        df = (request.args.get("date_from") or "").strip()
        dt = (request.args.get("date_to") or "").strip()
        return (df if _valid_date(df) else None,
                dt if _valid_date(dt) else None, df, dt)
    return first, last, first, last


@expenses_bp.route("/expenses")
@login_required
@require_perm("expense.view")
def index():
    """Everything except fuel, which has its own screen.

    Filtered by period first: a ledger without one shows the last N rows and a
    total nobody can compare to anything.
    """
    start, end, date_from, date_to = _period_bounds()
    fleet_id = request.args.get("fleet_id", type=int)

    q = _cash_expenses()
    if start:
        q = q.filter(Expense.date >= start)
    if end:
        q = q.filter(Expense.date <= end)
    if fleet_id:
        q = q.filter(Expense.fleet_id == fleet_id)
    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).limit(300).all()

    dq = CashMovement.query
    if start:
        dq = dq.filter(CashMovement.date >= start)
    if end:
        dq = dq.filter(CashMovement.date <= end)
    deposits = dq.order_by(CashMovement.date.desc(), CashMovement.id.desc()).all()

    return render_template(
        "expenses.html", expenses=expenses, deposits=deposits,
        total=sum(e.amount for e in expenses),
        deposited=sum(d.amount for d in deposits),
        balance=cash_balance(), fleet_id=fleet_id,
        date_from=date_from, date_to=date_to,
        fleets=_accessible_fleets(),
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
    if expense.stock_movement_id:
        # Same arrangement for a stock receipt: it is edited on the movement.
        flash("error|" + t["expense.err.stock_locked"])
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
    if expense.stock_movement_id:
        flash("error|" + t["expense.err.stock_locked"])
        return redirect(request.referrer or url_for("expenses.index"))
    fleet_id = expense.fleet_id
    db.session.delete(expense)
    log_action("DELETE", "expense", resource_id=xid, fleet_id=fleet_id,
               detail=f"Deleted expense #{xid}")
    db.session.commit()
    flash("success|" + t["expense.deleted"])
    return redirect(request.referrer or url_for("expenses.index"))


# ── Caisse: the printable report ─────────────────────────────────────────────

REPORT_LIMIT = 1000   # rows in one document; flagged on the page when reached


def _caisse_report(start, end, fleet_id):
    """The cash book for a window: what was in the box when it opened, every
    movement in date order with the balance after each, and what is left.

    Ordered oldest first — a running balance only reads forward — which is the
    opposite of the screen, where the newest line matters most.
    """
    paid_in_before = spent_before = 0
    if start:
        paid_in_before = db.session.query(db.func.coalesce(
            db.func.sum(CashMovement.amount), 0)).filter(
            CashMovement.date < start).scalar() or 0
        spent_before = db.session.query(db.func.coalesce(
            db.func.sum(Expense.amount), 0)).filter(
            Expense.category.notin_([FUEL_CATEGORY, MAINTENANCE_CATEGORY,
                                     PARTS_CATEGORY]),
            Expense.date < start).scalar() or 0
    opening = paid_in_before - spent_before

    dq = CashMovement.query
    xq = _cash_expenses()
    if start:
        dq = dq.filter(CashMovement.date >= start)
        xq = xq.filter(Expense.date >= start)
    if end:
        dq = dq.filter(CashMovement.date <= end)
        xq = xq.filter(Expense.date <= end)
    if fleet_id:
        xq = xq.filter(Expense.fleet_id == fleet_id)

    rows = []
    for d in dq.all():
        rows.append({"date": d.date, "label": d.source or get_t()["caisse.deposit"],
                     "quantity": None,
                     "detail": d.note or d.reference, "method": d.method,
                     "in": d.amount, "out": 0})
    for x in xq.all():
        rows.append({"date": x.date, "label": x.label or "—",
                     "quantity": x.quantity,
                     "detail": x.description or x.payment_reference,
                     "method": x.payment_method,
                     "in": 0, "out": x.amount})
    rows.sort(key=lambda r: r["date"])
    truncated = len(rows) > REPORT_LIMIT
    rows = rows[:REPORT_LIMIT]

    running = opening
    for r in rows:
        running += r["in"] - r["out"]
        r["balance"] = running

    return dict(
        rows=rows, opening=opening,
        total_in=sum(r["in"] for r in rows),
        total_out=sum(r["out"] for r in rows),
        closing=running, truncated=truncated)


@expenses_bp.route("/expenses/export.print")
@login_required
@require_perm("report.export_pdf")
def export_print():
    """The cash book, laid out for the browser to print — same arrangement as
    the pointage export, and the filters come from the query string so what
    prints does not depend on what the page happens to show."""
    t = get_t()
    start, end, date_from, date_to = _period_bounds()
    fleet_id = request.args.get("fleet_id", type=int)

    parts = []
    if fleet_id:
        fl = db.session.get(Fleet, fleet_id)
        if fl:
            parts.append(fl.name)
    if start or end:
        parts.append("%s → %s" % (start or "…", end or "…"))
    subtitle = " · ".join(parts) if parts else t.get("caisse.all_periods",
                                                    "Toutes périodes")

    return render_template(
        "caisse_print.html",
        back_url=url_for("expenses.index", **request.args.to_dict()),
        subtitle=subtitle,
        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        **_caisse_report(start, end, fleet_id))


# ── Caisse: money paid in ────────────────────────────────────────────────────


def _read_deposit_form():
    """A deposit into the cash box: when, how much, from whom, and how."""
    t = get_t()
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

    method = (request.form.get("method") or "").strip()
    if method not in CASH_METHODS:
        return None, t["expense.err.payment_required"]

    return dict(
        kind="depot", date=date_str, amount=amount, currency="GNF", method=method,
        source=(request.form.get("source") or "").strip() or None,
        reference=(request.form.get("reference") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    ), None


def _render_deposit_form(deposit, error=None):
    tpl = "_deposit_form.html" if is_modal_request() else "deposit_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, deposit=deposit, error=error,
                           methods=CASH_METHODS,
                           today=date.today().isoformat()), status


@expenses_bp.route("/caisse/depots/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def deposit_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_deposit_form()
        if error:
            return _render_deposit_form(None, error)
        d = CashMovement(created_by=current_user.id, **data)
        db.session.add(d)
        db.session.flush()
        log_action("CREATE", "cash_movement", resource_id=d.id,
                   detail=f"Deposit {d.amount} GNF into the cash box")
        db.session.commit()
        flash("success|" + t.get("caisse.deposit_created", "Dépôt enregistré."))
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
    return _render_deposit_form(None)


@expenses_bp.route("/caisse/depots/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_perm("expense.edit")
def deposit_edit(did):
    d = db.session.get(CashMovement, did)
    if not d:
        abort(404)
    t = get_t()
    if request.method == "POST":
        data, error = _read_deposit_form()
        if error:
            return _render_deposit_form(d, error)
        for k, v in data.items():
            setattr(d, k, v)
        log_action("UPDATE", "cash_movement", resource_id=d.id,
                   detail=f"Edited deposit #{d.id}")
        db.session.commit()
        flash("success|" + t.get("caisse.deposit_updated", "Dépôt modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
    return _render_deposit_form(d)


@expenses_bp.route("/caisse/depots/<int:did>/delete", methods=["POST"])
@login_required
@require_perm("expense.delete")
def deposit_delete(did):
    d = db.session.get(CashMovement, did)
    if not d:
        abort(404)
    db.session.delete(d)
    log_action("DELETE", "cash_movement", resource_id=did,
               detail=f"Deleted deposit #{did}")
    db.session.commit()
    flash("success|" + get_t().get("caisse.deposit_deleted", "Dépôt supprimé."))
    return redirect(request.referrer or url_for("expenses.index"))


# ── Carburant ────────────────────────────────────────────────────────────────


def _render_fuel_form(expense, error=None):
    tpl = "_fuel_form.html" if is_modal_request() else "fuel_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, expense=expense, error=error,
                           **_fuel_form_context(expense)), status


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
