"""Expenses blueprint — the cash box and the ledger behind it.

/expenses is a till: money is paid in, and the costs entered there spend it.
Everything logged is a company cost, filed as société or terrain.

Costs raised elsewhere land in the same table and show read-only where they
belong: a service writes "entretien" alongside its record, a stock receipt
writes "pieces". Neither is paid out of this till, so neither touches its
balance. Fuel used to be here too; it is litres in the citerne module now and
carries no money at all.
"""
import calendar
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, needs_approval, require_perm, submit_change, with_current_fleet)
from models import (CashAccount, CashMovement, Expense, Fleet, Site,
                    Vehicle, db)

expenses_bp = Blueprint("expenses", __name__)

# Category codes (labels via expense.cat.<code>).
# Fuel used to be logged as an expense on a screen of its own. It is tracked in
# litres by the citerne module now and carries no money, so nothing writes this
# category any more — the code stays to keep old rows out of the cash box.
FUEL_CATEGORY = "fuel"
MAINTENANCE_CATEGORY = "entretien"  # written by the maintenance blueprint only
PARTS_CATEGORY = "pieces"        # written by the stock blueprint only (a receipt)

# A cost is filed by where it happened, not by a kind someone picks: with a
# site it is terrain, without one it is the office. Nobody chooses these — they
# are derived — but they stay as the stored category so old rows still read.
FIELD_CATEGORY = "terrain"
OFFICE_CATEGORY = "societe"

# What manual costs used to be filed under, before the two above. Nothing new
# lands here, but old rows keep theirs so their label still reads.
EXPENSE_CATEGORIES = ["accident", "lavage", "autre"]

# Categories the app writes itself, from its own screens. A cost in one of them
# was not paid out of the cash box, so it never touches its balance.
SYSTEM_CATEGORIES = [FUEL_CATEGORY, MAINTENANCE_CATEGORY, PARTS_CATEGORY]

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


def category_label(code):
    """A stored category's name for display. These are not picked any more — a
    cost is filed by its site — so this only labels what other modules write
    and what older rows carry."""
    return get_t().get("expense.cat." + code, code)


def active_sites():
    """The places money gets spent, as the cashier keeps them."""
    return (Site.query.filter(Site.is_active.is_(True))
            .order_by(Site.sort_order, Site.name).all())


def active_accounts():
    """Where the box's money comes from and goes back to."""
    return (CashAccount.query.filter(CashAccount.is_active.is_(True))
            .order_by(CashAccount.sort_order, CashAccount.name).all())


def _accessible_vehicles():
    """Machines a cost can be pinned to. A deleted machine keeps its costs but
    is no longer offered."""
    q = Vehicle.query.filter(Vehicle.deleted_at.is_(None))
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _ids(name):
    """Repeated query params as ints — the filters are tick boxes, so a site or
    a machine can be asked for several at a time."""
    out = []
    for raw in request.args.getlist(name):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            pass
    return out


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
    """A cash cost: what it was, where, for which machine, and how it was paid.

    Nothing is asked that can be worked out. The category is not picked — a cost
    with a site is terrain, one without is the office — and the part count is
    offered only where it makes sense, on a cost that happened somewhere.

    Returns (data, None) or (None, err).
    """
    t = get_t()
    common, error = _common_fields(t, CASH_METHODS)
    if error:
        return None, error

    site_id = request.form.get("site_id", type=int) or None
    if site_id and not Site.query.filter_by(id=site_id, is_active=True).first():
        return None, t.get("site.err.unknown", "Choisissez un site actif.")

    vehicle_id = request.form.get("vehicle_id", type=int) or None
    if vehicle_id:
        v = db.session.get(Vehicle, vehicle_id)
        fids = current_user_fleet_ids()
        if not v or (fids is not None and v.fleet_id not in fids):
            return None, t["expense.err.vehicle_required"]

    # Only a cost that happened on a site tends to have parts against it.
    quantity = None
    if site_id:
        raw_qty = (request.form.get("quantity") or "").strip().replace(",", ".")
        if raw_qty:
            try:
                quantity = float(raw_qty)
            except ValueError:
                return None, t.get("expense.err.quantity", "Quantité invalide.")
            if quantity <= 0:
                return None, t.get("expense.err.quantity", "Quantité invalide.")

    common.update(vehicle_id=vehicle_id, fleet_id=None, label=None, operator=None,
                  supplier=None, site_id=site_id, liters=None, quantity=quantity,
                  category=FIELD_CATEGORY if site_id else OFFICE_CATEGORY)
    return common, None


def _form_context(expense):
    # The site of the last cost logged, to save picking the same one all day.
    last = (Expense.query.filter(Expense.site_id.isnot(None))
            .order_by(Expense.id.desc()).first())
    return {
        "payment_methods": CASH_METHODS,
        "sites": active_sites(),
        "vehicles": _accessible_vehicles(),
        "last_site_id": last.site_id if last else None,
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
    return _scoped_expenses().filter(Expense.category.notin_(SYSTEM_CATEGORIES))


def _movement_total(kind):
    return db.session.query(db.func.coalesce(
        db.func.sum(CashMovement.amount), 0)).filter(
        CashMovement.kind == kind).scalar() or 0


def cash_balance():
    """What is left in the box: paid in, less taken back out, less spent. Not
    bounded by the period on screen — a balance carries over."""
    spent = db.session.query(db.func.coalesce(
        db.func.sum(Expense.amount), 0)).filter(
        Expense.category.notin_(SYSTEM_CATEGORIES)).scalar() or 0
    return _movement_total("depot") - _movement_total("retrait") - spent


def account_balances():
    """Per account: what the box has taken from it, less what it has given back.

    On a repayable account that figure is what is still owed — the boss's own
    money sitting in the till, an agent waiting to be paid back. On the
    company's own account it is simply what the house has put in.
    """
    rows = (db.session.query(
                CashMovement.account_id, CashMovement.kind,
                db.func.coalesce(db.func.sum(CashMovement.amount), 0))
            .filter(CashMovement.account_id.isnot(None))
            .group_by(CashMovement.account_id, CashMovement.kind).all())
    per = {}
    for acc_id, kind, amount in rows:
        e = per.setdefault(acc_id, {"in": 0, "out": 0})
        e["in" if kind == "depot" else "out"] += int(amount or 0)
    out = []
    for acc in CashAccount.query.order_by(CashAccount.sort_order,
                                          CashAccount.name).all():
        e = per.get(acc.id)
        if not e:
            continue
        out.append({"account": acc, "in": e["in"], "out": e["out"],
                    "balance": e["in"] - e["out"]})
    return out


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
    site_ids, vehicle_ids, account_ids = _ids("site"), _ids("vehicle"), _ids("account")

    q = _cash_expenses()
    if start:
        q = q.filter(Expense.date >= start)
    if end:
        q = q.filter(Expense.date <= end)
    if site_ids:
        q = q.filter(Expense.site_id.in_(site_ids))
    if vehicle_ids:
        q = q.filter(Expense.vehicle_id.in_(vehicle_ids))
    # Asking for an account is asking about money in and out of it, which no
    # cost carries — so the costs drop out rather than being filtered wrongly.
    if account_ids:
        q = q.filter(db.false())
    expenses = q.order_by(Expense.date.desc(), Expense.id.desc()).limit(300).all()

    mq = CashMovement.query
    if start:
        mq = mq.filter(CashMovement.date >= start)
    if end:
        mq = mq.filter(CashMovement.date <= end)
    if account_ids:
        mq = mq.filter(CashMovement.account_id.in_(account_ids))
    # A site or a machine is a property of a cost, never of a movement.
    if site_ids or vehicle_ids:
        mq = mq.filter(db.false())
    movements = mq.order_by(CashMovement.date.desc(), CashMovement.id.desc()).all()

    return render_template(
        "expenses.html", expenses=expenses, movements=movements,
        total=sum(e.amount for e in expenses),
        deposited=sum(m.amount for m in movements if m.kind == "depot"),
        withdrawn=sum(m.amount for m in movements if m.kind == "retrait"),
        balance=cash_balance(), accounts_summary=account_balances(),
        date_from=date_from, date_to=date_to,
        sites=active_sites(), vehicles=_accessible_vehicles(),
        accounts=active_accounts(),
        site_ids=site_ids, vehicle_ids=vehicle_ids, account_ids=account_ids,
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


def _caisse_report(start, end, site_ids, vehicle_ids, account_ids):
    """The cash book for a window: what the box held when it opened, every
    movement in date order with the balance after each, and what is left.

    Ordered oldest first — a running balance only reads forward — which is the
    opposite of the screen, where the newest line matters most.
    """
    filtered = bool(site_ids or vehicle_ids or account_ids)

    def cost_q():
        q = _cash_expenses()
        if site_ids:
            q = q.filter(Expense.site_id.in_(site_ids))
        if vehicle_ids:
            q = q.filter(Expense.vehicle_id.in_(vehicle_ids))
        if account_ids:
            q = q.filter(db.false())
        return q

    def move_q():
        q = CashMovement.query
        if account_ids:
            q = q.filter(CashMovement.account_id.in_(account_ids))
        if site_ids or vehicle_ids:
            q = q.filter(db.false())
        return q

    # An opening balance only means something for the whole box. Narrowed to a
    # site or a machine the document is a list of costs, not a cash book, so it
    # starts from zero instead of quoting a figure that answers another question.
    opening = 0
    if start and not filtered:
        paid = db.session.query(db.func.coalesce(db.func.sum(CashMovement.amount), 0))
        opening = ((paid.filter(CashMovement.kind == "depot",
                                CashMovement.date < start).scalar() or 0)
                   - (paid.filter(CashMovement.kind == "retrait",
                                  CashMovement.date < start).scalar() or 0)
                   - (db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0))
                      .filter(Expense.category.notin_(SYSTEM_CATEGORIES),
                              Expense.date < start).scalar() or 0))

    cq, mq = cost_q(), move_q()
    if start:
        cq = cq.filter(Expense.date >= start)
        mq = mq.filter(CashMovement.date >= start)
    if end:
        cq = cq.filter(Expense.date <= end)
        mq = mq.filter(CashMovement.date <= end)

    rows = []
    for m in mq.all():
        rows.append({"date": m.date,
                     "label": (m.account.name if m.account
                               else get_t()["caisse.deposit"]),
                     "kind": m.kind, "site": None, "vehicle": None,
                     "quantity": None, "detail": m.note or m.reference,
                     "method": m.method,
                     "in": m.amount if m.kind == "depot" else 0,
                     "out": m.amount if m.kind == "retrait" else 0})
    for x in cq.all():
        rows.append({"date": x.date, "label": x.label or category_label(x.category),
                     "kind": "depense",
                     "site": x.site.name if x.site else None,
                     "vehicle": x.vehicle.code if x.vehicle else None,
                     "quantity": x.quantity,
                     "detail": x.description or x.payment_reference,
                     "method": x.payment_method, "in": 0, "out": x.amount})
    rows.sort(key=lambda r: r["date"])
    truncated = len(rows) > REPORT_LIMIT
    rows = rows[:REPORT_LIMIT]

    running = opening
    for r in rows:
        running += r["in"] - r["out"]
        r["balance"] = running

    return dict(
        rows=rows, opening=opening, filtered=filtered,
        total_in=sum(r["in"] for r in rows),
        total_spent=sum(r["out"] for r in rows if r["kind"] == "depense"),
        total_withdrawn=sum(r["out"] for r in rows if r["kind"] == "retrait"),
        closing=running, truncated=truncated,
        accounts_summary=account_balances())


@expenses_bp.route("/expenses/export.print")
@login_required
@require_perm("report.export_pdf")
def export_print():
    """The cash book, laid out for the browser to print — same arrangement as
    the pointage export, and the filters come from the query string so what
    prints does not depend on what the page happens to show."""
    t = get_t()
    start, end, date_from, date_to = _period_bounds()
    site_ids, vehicle_ids, account_ids = _ids("site"), _ids("vehicle"), _ids("account")

    parts = []
    if start or end:
        parts.append("%s → %s" % (start or "…", end or "…"))
    for model, ids, attr in ((Site, site_ids, "name"),
                             (Vehicle, vehicle_ids, "code"),
                             (CashAccount, account_ids, "name")):
        names = [getattr(o, attr) for o in
                 model.query.filter(model.id.in_(ids)).all()] if ids else []
        if names:
            parts.append(", ".join(names))
    subtitle = " · ".join(parts) if parts else t.get("caisse.all_periods",
                                                    "Toutes périodes")

    return render_template(
        "caisse_print.html",
        back_url=url_for("expenses.index", **request.args.to_dict(flat=False)),
        subtitle=subtitle,
        generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        **_caisse_report(start, end, site_ids, vehicle_ids, account_ids))


# ── The two short lists the cashier keeps ────────────────────────────────────
#
# Kept on the Dépenses page rather than in Administration: they are hers, she
# adds a site the day a new one opens, and sending her to another screen for a
# one-line list is how a list stops being kept up to date.


def _list_ctx(kind):
    """Sites and accounts differ by one tick box; everything else is shared."""
    model = Site if kind == "site" else CashAccount
    return model, ("site" if kind == "site" else "account")


@expenses_bp.route("/caisse/listes")
@login_required
@require_perm("expense.create")
def cash_lists():
    return render_template("cash_lists.html",
                           sites=Site.query.order_by(Site.sort_order, Site.name).all(),
                           accounts=CashAccount.query.order_by(
                               CashAccount.sort_order, CashAccount.name).all(),
                           used_sites={r[0] for r in db.session.query(
                               Expense.site_id).filter(
                               Expense.site_id.isnot(None)).distinct().all()},
                           used_accounts={r[0] for r in db.session.query(
                               CashMovement.account_id).filter(
                               CashMovement.account_id.isnot(None)).distinct().all()})


def _save_list_row(model, row, kind):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = model.query.filter(db.func.lower(model.name) == name.lower())
    if row:
        clash = clash.filter(model.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(model.sort_order)).scalar() or 0) + 1
        row = model(sort_order=nxt)
        db.session.add(row)
    row.name = name
    if kind == "account":
        row.is_repayable = request.form.get("is_repayable") is not None
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "cash_%s" % kind,
               resource_id=row.id, detail="%s %s '%s'" % (
                   "Created" if creating else "Updated", kind, row.name))
    db.session.commit()
    return None


def _render_list_form(row, kind, error=None):
    tpl = "_cash_list_form.html" if is_modal_request() else "cash_list_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, kind=kind, error=error), status


@expenses_bp.route("/caisse/listes/<kind>/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def cash_list_new(kind):
    if kind not in ("site", "account"):
        abort(404)
    model, _ = _list_ctx(kind)
    if request.method == "POST":
        error = _save_list_row(model, None, kind)
        if error:
            return _render_list_form(None, kind, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.cash_lists"))
    return _render_list_form(None, kind)


@expenses_bp.route("/caisse/listes/<kind>/<int:row_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def cash_list_edit(kind, row_id):
    if kind not in ("site", "account"):
        abort(404)
    model, _ = _list_ctx(kind)
    row = db.session.get(model, row_id)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_list_row(model, row, kind)
        if error:
            return _render_list_form(row, kind, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.cash_lists"))
    return _render_list_form(row, kind)


@expenses_bp.route("/caisse/listes/<kind>/<int:row_id>/<any(archive,reactivate,delete):what>",
                   methods=["POST"])
@login_required
@require_perm("expense.create")
def cash_list_action(kind, row_id, what):
    """Archive takes it out of the pickers and leaves its history named; delete
    is only for one nothing has ever pointed at."""
    if kind not in ("site", "account"):
        abort(404)
    model, _ = _list_ctx(kind)
    row = db.session.get(model, row_id)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        used = (Expense.query.filter_by(site_id=row.id).count() if kind == "site"
                else CashMovement.query.filter_by(account_id=row.id).count())
        if used:
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(url_for("expenses.cash_lists"))
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "cash_%s" % kind, resource_id=row_id,
                   detail="Deleted %s '%s'" % (kind, name))
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "cash_%s" % kind, resource_id=row_id,
                   detail="%s %s '%s'" % (what.title(), kind, row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(url_for("expenses.cash_lists"))


# ── Caisse: money in and out ─────────────────────────────────────────────────


def _read_movement_form(kind):
    """Money in or money out of the box: when, how much, and which account it
    came from or went back to."""
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

    account_id = request.form.get("account_id", type=int) or None
    if not account_id or not CashAccount.query.filter_by(
            id=account_id, is_active=True).first():
        return None, t.get("account.err.required", "Choisissez un compte.")

    return dict(
        kind=kind, date=date_str, amount=amount, currency="GNF", method=method,
        account_id=account_id,
        reference=(request.form.get("reference") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    ), None


def _render_movement_form(movement, kind, error=None):
    tpl = "_movement_form.html" if is_modal_request() else "movement_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, movement=movement, kind=kind, error=error,
                           methods=CASH_METHODS, accounts=active_accounts(),
                           today=date.today().isoformat()), status


def _movement_route(kind, movement=None):
    """Create or edit one, whichever direction — the two differ only in wording
    and in which way the balance moves."""
    t = get_t()
    if request.method == "POST":
        data, error = _read_movement_form(kind)
        if error:
            return _render_movement_form(movement, kind, error)
        if movement is None:
            movement = CashMovement(created_by=current_user.id, **data)
            db.session.add(movement)
            db.session.flush()
            action, msg = "CREATE", "caisse.%s_created" % kind
        else:
            for k, v in data.items():
                setattr(movement, k, v)
            action, msg = "UPDATE", "caisse.%s_updated" % kind
        log_action(action, "cash_movement", resource_id=movement.id,
                   detail="%s %s %d GNF" % (action.title(), kind, movement.amount))
        db.session.commit()
        flash("success|" + t.get(msg, "Enregistré."))
        return modal_ok() if is_modal_request() else redirect(url_for("expenses.index"))
    return _render_movement_form(movement, kind)


def _get_movement_or_404(mid, kind=None):
    m = db.session.get(CashMovement, mid)
    if not m or (kind and m.kind != kind):
        abort(404)
    return m


@expenses_bp.route("/caisse/depots/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def deposit_new():
    return _movement_route("depot")


@expenses_bp.route("/caisse/retraits/new", methods=["GET", "POST"])
@login_required
@require_perm("expense.create")
def withdrawal_new():
    return _movement_route("retrait")


@expenses_bp.route("/caisse/mouvements/<int:did>/edit", methods=["GET", "POST"])
@login_required
@require_perm("expense.edit")
def deposit_edit(did):
    m = _get_movement_or_404(did)
    return _movement_route(m.kind, m)


@expenses_bp.route("/caisse/mouvements/<int:did>/delete", methods=["POST"])
@login_required
@require_perm("expense.delete")
def deposit_delete(did):
    d = _get_movement_or_404(did)
    kind, amount = d.kind, d.amount
    db.session.delete(d)
    log_action("DELETE", "cash_movement", resource_id=did,
               detail="Deleted %s %d GNF" % (kind, amount))
    db.session.commit()
    flash("success|" + get_t().get("caisse.movement_deleted", "Mouvement supprimé."))
    return redirect(request.referrer or url_for("expenses.index"))
