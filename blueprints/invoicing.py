"""Facturation — the client side of the money, in two tabs.

Clients: who the machines work for. The mirror of the lessor side of
Fournisseurs: a supplier can lease machines to the company and the app knows
what each costs; a client has machines placed with it and the app knows what
each earns. One machine may be both at once -- leased from Diallo, working
for VIVO -- with a price on each side.

A client is created with its details only; its machines are added one at a
time on its Machines tab, each with a rate, GNF per worked unit, and the date
it starts. Rates are dated history (see billing.py): a new rate appends a row
with its date of effect, the old one stays for the months it applied. Codes
are CL-001, CL-002... issued by the app, never typed.

Factures: the register of bills issued. "Nouvelle facture" works a month out
for a client -- each entry's units × the machine's rate on that day, one line
per machine and rate -- and issuing freezes those lines under a number
(FAC-2026-001, per year), so a daily entry corrected afterwards never moves a
bill already sent. A cancelled bill stays in the register, struck through,
and frees its month.
Règlements: what the client paid against a bill, in one or several
payments; the bill's state (à régler, partielle, réglée, en retard) is read
off them, never stored.
"""
import calendar
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

import billing
from amount_words import amount_in_words
from app import (_get_setting, _num, current_lang, current_user_fleet_ids, get_t, has_perm,
                 is_modal_request, log_action, modal_ok, parse_amount, parse_money, require_perm)
from blueprints.expenses import PAYMENT_METHODS, active_accounts, method_error, methods_of
from ledger import ensure_client_account, labels_for, read_code, revenue_accounts
from models import (AppSetting, BillingTag, BillingTagRate, CashAccount, Client, ClientInvoice,
                    ClientInvoiceLine, PurchaseOrder, ClientPayment, ClientRate, DailyEntry,
                    LedgerAccount, Vehicle, VehicleTag, db)

invoicing_bp = Blueprint("invoicing", __name__)

CODE_PREFIX = "CL-"
# "commandes": the purchase orders waiting for the finance signature, shown
# to whoever gives it -- the person who keeps Facturation.
# The clients used to be a tab here; they have a page of their own now.
TABS = ("factures", "commandes")

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
}


def month_label(period, lang="fr"):
    """"juillet 2026" for "2026-07"."""
    try:
        y, m = period.split("-")
        return "%s %s" % (MONTHS.get(lang, MONTHS["fr"])[int(m) - 1], y)
    except (ValueError, IndexError, AttributeError):
        return period


def _valid_month(s):
    try:
        datetime.strptime(s, "%Y-%m")
        return True
    except (TypeError, ValueError):
        return False


def _valid_date(s):
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def _clients(include_archived=False):
    q = Client.query.order_by(Client.sort_order, Client.name)
    if not include_archived:
        q = q.filter(Client.is_active.is_(True))
    return q.all()


def month_bounds(month):
    """("2026-07-01", "2026-07-31") for "2026-07"."""
    y, m = (int(x) for x in month.split("-"))
    return "%04d-%02d-01" % (y, m), "%04d-%02d-%02d" % (y, m, calendar.monthrange(y, m)[1])


def period_label(inv, lang="fr"):
    """What a bill covers, in words: "du 20 juin au 20 juillet 2026" when it
    names its days, else the month, as bills from before did."""
    if inv.period_start and inv.period_end:
        return days_label(inv.period_start, inv.period_end, lang)
    return month_label(inv.period, lang)


def days_label(start, end, lang="fr"):
    names = MONTHS.get(lang, MONTHS["fr"])
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    first, last = month_bounds(start[:7])
    if start == first and end == last:
        return month_label(start[:7], lang)
    left = "%d %s" % (a.day, names[a.month - 1]) + ("" if a.year == b.year else " %d" % a.year)
    right = "%d %s %d" % (b.day, names[b.month - 1], b.year)
    return ("du %s au %s" if lang == "fr" else "from %s to %s") % (left, right)


def _month_lines(client, month):
    """The month worked out, see _period_lines."""
    start, end = month_bounds(month)
    return _period_lines(client, start, end)


def _period_lines(client, start, end):
    """The days from `start` to `end` worked out, one line per machine and
    price in force.

    A per-unit machine bills units × rate; a price that changed within the
    span gives it two lines. A monthly machine bills its worked days — a
    half day marked in the saisie counts 0.5, two saisies the same day stay
    one day — each day at monthly price ÷ days of that very month (February
    divides by 28), so a span over two months gives one line per month.

    Lines carry their tarif's currency; a bill takes one currency's lines.
    Returns (lines, any_missing); a line with rate None is one an entry had
    no price for, and blocks issuing."""
    book = billing.rate_book(client.id)
    fids = current_user_fleet_ids()
    groups = {}
    month_days = {}   # a monthly line's worked days: {date: weight}
    for v in client.live_machines:
        if fids is not None and v.fleet_id not in fids:
            continue
        unit_type = v.category.unit_type
        entries = (DailyEntry.query.filter_by(vehicle_id=v.id)
                   .filter(DailyEntry.date >= start, DailyEntry.date <= end)
                   .order_by(DailyEntry.date).all())
        for e in entries:
            basis, cur, rate = billing.price_on(book, v.id, e.date)
            u = billing.entry_units(e, unit_type)
            if basis == "month":
                if u <= 0 and not e.half_day:
                    continue
                key = (v.code, rate, cur, e.date[:7])
                days = month_days.setdefault(key, {})
                days[e.date] = max(days.get(e.date, 0.0), billing.day_weight(e))
                groups.setdefault(key, {
                    "vehicle_id": v.id, "vehicle_code": v.code,
                    "category_label": v.category.label_fr, "unit_type": "days",
                    "units": 0.0, "rate": None, "amount": 0,
                    "currency": cur, "monthly": rate})
            else:
                if u <= 0:
                    continue
                key = (v.code, rate, cur or "GNF", "")
                g = groups.setdefault(key, {
                    "vehicle_id": v.id, "vehicle_code": v.code,
                    "category_label": v.category.label_fr, "unit_type": unit_type,
                    "units": 0.0, "rate": rate, "amount": 0,
                    "currency": cur, "monthly": None})
                g["units"] += u
    lines = []
    for key, g in groups.items():
        if g["monthly"] is not None:
            dim = billing.days_in_month(key[3] + "-01")
            g["units"] = sum(month_days[key].values())
            # The day price as the paper shows it; the amount from the exact
            # fraction, rounded once, whole francs or dollars.
            g["rate"] = (Decimal(g["monthly"]) / Decimal(dim)).quantize(Decimal("0.01"))
            g["amount"] = Decimal(int(round(g["monthly"] * g["units"] / dim)))
        else:
            g["amount"] = Decimal(int(round(g["units"] * g["rate"]))) if g["rate"] is not None else Decimal(0)
        lines.append(g)
    lines.sort(key=lambda g: (g["vehicle_code"], Decimal(g["rate"] or 0)))
    any_missing = any(g["rate"] is None for g in lines)
    return lines, any_missing


def _open_invoice(client_id, period):
    """The bill already issued for that client and month, if one stands."""
    return (ClientInvoice.query.filter_by(client_id=client_id, period=period, is_opening=False)
            .filter(ClientInvoice.status != "cancelled").first())


def _machine_overlap(client_id, start, end, vehicle_ids):
    """A standing bill of this client that already bills one of THESE
    machines over any of these days: the same work must not be billed
    twice. Bills of other machines — the dollar bill beside the franc
    bill — and bills of free lines never stand in the way."""
    for inv in (ClientInvoice.query.filter_by(client_id=client_id, is_opening=False)
                .filter(ClientInvoice.status != "cancelled").all()):
        if not any((l.kind or "machine") == "machine" and l.vehicle_id in vehicle_ids
                   for l in inv.lines):
            continue
        a, b = (inv.period_start, inv.period_end) if inv.period_start else month_bounds(inv.period)
        if a <= end and start <= b:
            return inv
    return None


def _read_free_lines(form):
    """The typed lines of a bill. Returns (lines, None) or (None, error).
    A row with nothing in it is skipped."""
    t = get_t()
    titles, descs = form.getlist("free_title"), form.getlist("free_description")
    qtys, prices, discounts = form.getlist("free_qty"), form.getlist("free_price"), form.getlist("free_discount")
    out = []
    for i in range(max(len(titles), len(descs), len(qtys), len(prices))):
        g = lambda lst: (lst[i] if i < len(lst) else "").strip()
        title, desc, qty_raw, price_raw, disc_raw = g(titles), g(descs), g(qtys), g(prices), g(discounts)
        if not (title or desc or price_raw):
            continue
        if not (title or desc):
            return None, t["cinv.err.free_desc"]
        try:
            qty = float((qty_raw or "1").replace(",", "."))
        except ValueError:
            qty = 0
        if qty <= 0:
            return None, t["cinv.err.free_qty"]
        price = parse_money(price_raw)
        if price is None or price < 0:
            return None, t["cinv.err.free_price"]
        gross = (Decimal(str(qty)) * price).quantize(Decimal("0.01"))
        discount = parse_money(disc_raw) if disc_raw else Decimal(0)
        if discount is None or discount < 0 or discount > gross:
            return None, t["po.err.discount"]
        out.append(dict(title=title[:160] or None, description=desc or None, units=qty,
                        rate=price, discount=discount, amount=gross - discount))
    return out, None


def _next_number(issue_date):
    """FAC-2026-004 after FAC-2026-003, per year of issue. Read off the
    numbers in use; the unique constraint catches a tie."""
    prefix = "FAC-%s-" % issue_date[:4]
    with db.session.no_autoflush:
        taken = [n[0] for n in db.session.query(ClientInvoice.number)
                 .filter(ClientInvoice.number.like(prefix + "%")).all()]
    highest = 0
    for n in taken:
        try:
            highest = max(highest, int(n[len(prefix):]))
        except ValueError:
            pass
    return "%s%03d" % (prefix, highest + 1)


@invoicing_bp.route("/facturation")
@login_required
@require_perm("invoicing.view")
def index():
    # Imported here: purchases imports the stock and bill blueprints, which
    # need not load for the rest of this one.
    from blueprints.purchases import user_approves
    tab = request.args.get("tab")
    if tab == "clients":
        # An old link to the tab that became the clients' own page.
        return redirect(url_for("client_reports.clients", archives=request.args.get("archives") or None))
    clients = _clients()
    signs_orders = user_approves("finance")
    if tab == "commandes" and not signs_orders:
        tab = None
    if tab not in TABS:
        tab = "factures"
    show_archived = request.args.get("archives") == "1"
    if tab == "clients" and show_archived:
        clients = _clients(include_archived=True)
    archived = Client.query.filter(Client.is_active.is_(False)).count()

    today = date.today().isoformat()
    invoices, pagination = [], None
    billed = received = remaining = overdue_count = opening = 0
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    date_from = date_from if _valid_date(date_from) else ""
    date_to = date_to if _valid_date(date_to) else ""
    client_ids = []
    for raw in request.args.getlist("client"):
        try:
            client_ids.append(int(raw))
        except (TypeError, ValueError):
            pass
    status = request.args.get("status") or ""
    if status not in ("open", "overdue", "paid", "cancelled"):
        status = ""
    search = (request.args.get("q") or "").strip()
    if tab == "factures":
        q = ClientInvoice.query
        if date_from:
            q = q.filter(ClientInvoice.date >= date_from)
        if date_to:
            q = q.filter(ClientInvoice.date <= date_to)
        if client_ids:
            q = q.filter(ClientInvoice.client_id.in_(client_ids))
        if search:
            # What someone remembers about a bill: its number, its subject,
            # the client's reference, or the client's code or name.
            like = "%" + search + "%"
            q = q.join(Client).filter(db.or_(
                ClientInvoice.number.ilike(like), ClientInvoice.subject.ilike(like),
                ClientInvoice.client_ref.ilike(like),
                Client.code.ilike(like), Client.name.ilike(like)))
        paid = (db.select(db.func.coalesce(db.func.sum(ClientPayment.amount), 0))
                .where(ClientPayment.invoice_id == ClientInvoice.id)
                .correlate(ClientInvoice).scalar_subquery())
        # Paid, never past the bill's own total. A CASE rather than min(a, b):
        # Postgres has no two-argument min, only SQLite does.
        capped = db.case((paid > ClientInvoice.total, ClientInvoice.total), else_=paid)
        if status == "paid":
            q = q.filter(ClientInvoice.status != "cancelled", paid >= ClientInvoice.total)
        elif status in ("open", "overdue"):
            q = q.filter(ClientInvoice.status != "cancelled", paid < ClientInvoice.total)
            if status == "overdue":
                q = q.filter(ClientInvoice.due_date.isnot(None), ClientInvoice.due_date < today)
        elif status == "cancelled":
            q = q.filter(ClientInvoice.status == "cancelled")

        # Totals off the query, not off the page: past 50 bills the figures
        # at the top would otherwise cover only the slice on screen. A
        # cancelled bill counts for nothing.
        live = q.filter(ClientInvoice.status != "cancelled")
        # An old balance is owed and collected like a bill, but it was not
        # billed in the period: it stands apart from "Facturé".
        is_new = db.case((ClientInvoice.is_opening.is_(True), 0), else_=ClientInvoice.total)
        is_old = db.case((ClientInvoice.is_opening.is_(True), ClientInvoice.total), else_=0)
        sums = live.with_entities(
            db.func.coalesce(db.func.sum(is_new), 0),
            db.func.coalesce(db.func.sum(capped), 0),
            db.func.coalesce(db.func.sum(is_old), 0)).one()
        billed, received, opening = (sums[0] or 0), (sums[1] or 0), (sums[2] or 0)
        remaining = max(billed + opening - received, 0)
        overdue_count = live.filter(paid < ClientInvoice.total, ClientInvoice.due_date.isnot(None),
                                    ClientInvoice.due_date < today).count()
        pagination = (q.order_by(ClientInvoice.date.desc(), ClientInvoice.id.desc())
                      .paginate(page=request.args.get("page", 1, type=int), per_page=50, error_out=False))
        invoices = pagination.items

    # What each client still owes, in one grouped query rather than one per row.
    owed = {}
    if tab == "clients":
        paid = (db.select(db.func.coalesce(db.func.sum(ClientPayment.amount), 0))
                .where(ClientPayment.invoice_id == ClientInvoice.id)
                .correlate(ClientInvoice).scalar_subquery())
        # Paid, never past the bill's own total. A CASE rather than min(a, b):
        # Postgres has no two-argument min, only SQLite does.
        capped = db.case((paid > ClientInvoice.total, ClientInvoice.total), else_=paid)
        rows = (db.session.query(ClientInvoice.client_id, db.func.count(ClientInvoice.id),
                                 db.func.coalesce(db.func.sum(ClientInvoice.total - capped), 0))
                .filter(ClientInvoice.status != "cancelled")
                .group_by(ClientInvoice.client_id).all())
        owed = {r[0]: {"count": int(r[1]), "remaining": (r[2] or 0)} for r in rows}

    orders = []
    if signs_orders:
        orders = (PurchaseOrder.query.filter(PurchaseOrder.status == "pending_finance")
                  .order_by(PurchaseOrder.date, PurchaseOrder.id).all())

    kept = request.args.to_dict(flat=False)
    kept.pop("tab", None)
    kept.pop("page", None)
    tab_urls = {name: url_for("invoicing.index", tab=name, **kept) for name in TABS}
    tab_urls["commandes"] = url_for("invoicing.index", tab="commandes")
    return render_template("invoicing.html", tab=tab, tab_urls=tab_urls,
                           clients=clients, invoices=invoices, pagination=pagination,
                           billed=billed, received=received, remaining=remaining, opening=opening,
                           overdue_count=overdue_count, owed=owed,
                           date_from=date_from, date_to=date_to, client_ids=client_ids,
                           status=status, search=search, today=today,
                           filtered=bool(date_from or date_to or client_ids or status or search),
                           show_archived=show_archived, archived=archived,
                           signs_orders=signs_orders, orders=orders)


# ── Issuing a bill ───────────────────────────────────────────────────────────

@invoicing_bp.route("/facturation/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.view")
def invoice_new():
    """Work a period out for a client and issue the bill. The period is a
    span of days (20 June to 20 July), or a month as before. The bill holds
    the machines' lines worked out from the daily entries, typed free lines,
    or both."""
    clients = _clients()
    if not clients:
        return redirect(url_for("client_reports.clients"))
    cid = request.values.get("client_id", type=int)
    client = next((c for c in clients if c.id == cid), None) or clients[0]
    month = request.values.get("month", "")
    if not _valid_month(month):
        month = datetime.utcnow().strftime("%Y-%m")
    start = (request.values.get("period_start") or "").strip()
    end = (request.values.get("period_end") or "").strip()
    if not (_valid_date(start) and _valid_date(end) and start <= end):
        start, end = month_bounds(month)
    month = end[:7]
    all_lines, _ = _period_lines(client, start, end)
    currencies = sorted({g["currency"] for g in all_lines if g["currency"]})
    cur = request.values.get("cur") or ""
    if cur not in currencies:
        cur = "GNF" if ("GNF" in currencies or not currencies) else currencies[0]
    # This bill's lines: the chosen currency's, plus any day no price covers
    # (those must be seen, whichever currency is picked).
    lines = [g for g in all_lines if g["currency"] in (None, cur)]
    total = sum((g["amount"] for g in lines), Decimal(0))
    any_missing = any(g["rate"] is None for g in lines)
    existing = _machine_overlap(client.id, start, end,
                                {g["vehicle_id"] for g in lines})
    t = get_t()

    if request.method == "POST":
        if not has_perm("invoicing.manage"):
            abort(403)
        back = url_for("invoicing.invoice_new", client_id=client.id, month=month, cur=cur)
        # Machine lines are billed unless the form says otherwise; a form
        # from before free lines came says nothing, and billed them.
        flags = request.form.getlist("use_machines")
        use_machines = ("1" in flags if flags else True) and bool(lines)
        free, error = _read_free_lines(request.form)
        if error:
            flash("error|" + error)
            return redirect(back)
        if use_machines and existing:
            flash("error|" + t["cinv.already"] + " " + existing.number)
            return redirect(url_for("invoicing.invoice_detail", iid=existing.id))
        if use_machines and any_missing:
            flash("error|" + t["cinv.cannot_missing"])
            return redirect(back)
        if not use_machines and not free:
            flash("error|" + t["cinv.cannot_empty"])
            return redirect(back)
        issue_date = (request.form.get("date") or "").strip() or date.today().isoformat()
        due = (request.form.get("due_date") or "").strip() or None
        if not _valid_date(issue_date) or (due and not _valid_date(due)):
            flash("error|" + t.get("client.err.date", "Date invalide."))
            return redirect(back)
        # The revenue account, for the journal: one pick, written on every
        # line, since a bill's lines are all the same kind of sale.
        ledger_code, ok = read_code(request.form, allowed=revenue_accounts())
        if not ok:
            flash("error|" + t["ledger.err.unknown"])
            return redirect(back)
        text = lambda k, n: (request.form.get(k) or "").strip()[:n] or None
        grand = (total if use_machines else Decimal(0)) + sum((f["amount"] for f in free), Decimal(0))
        # A dollar bill fixes its francs once and for all: the BCRG rate of
        # the day is typed on it, and the GNF total is what the client owes.
        fx_rate = fx_total = None
        total_gnf = grand
        if cur != "GNF":
            raw = (request.form.get("fx_rate") or "").strip().replace(" ", "").replace(",", ".")
            try:
                fx_rate = Decimal(raw)
            except InvalidOperation:
                fx_rate = None
            if fx_rate is None or fx_rate <= 0:
                flash("error|" + t["cinv.err.fx"])
                return redirect(back)
            fx_total = grand
            total_gnf = (fx_total * fx_rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        inv = ClientInvoice(client_id=client.id, number=_next_number(issue_date), period=month,
                            period_start=start, period_end=end,
                            currency=cur, fx_rate=fx_rate, fx_total=fx_total,
                            date=issue_date, due_date=due, total=total_gnf,
                            subject=text("subject", 120), client_ref=text("client_ref", 60),
                            contract_no=text("contract_no", 60), attention=text("attention", 120),
                            equipments=text("equipments", 120), work_purpose=text("work_purpose", 80),
                            equipment_type=text("equipment_type", 80), availability=text("availability", 40),
                            intro=text("intro", 255),
                            contact_name=text("contact_name", 120), contact_phone=text("contact_phone", 60),
                            note=(request.form.get("note") or "").strip() or None,
                            created_by=current_user.id)
        if use_machines:
            for g in lines:
                inv.lines.append(ClientInvoiceLine(
                    kind="machine", vehicle_id=g["vehicle_id"], vehicle_code=g["vehicle_code"],
                    category_label=g["category_label"], unit_type=g["unit_type"],
                    units=g["units"], rate=g["rate"], amount=g["amount"], discount=0,
                    ledger_code=ledger_code))
        for f in free:
            inv.lines.append(ClientInvoiceLine(kind="free", ledger_code=ledger_code, **f))
        db.session.add(inv)
        for attempt in range(3):
            try:
                db.session.commit()
                break
            except IntegrityError:
                db.session.rollback()
                if attempt == 2:
                    abort(500)
                inv.number = _next_number(issue_date)
                inv = db.session.merge(inv)
        # His first invoice gives the client his account on the plan.
        if ensure_client_account(client, current_user.id):
            db.session.commit()
        log_action("CREATE", "client_invoice", resource_id=inv.id,
                   detail="Issued %s to %s for %s to %s: %s GNF" % (inv.number, client.name, start, end, inv.total))
        db.session.commit()
        flash("success|" + t["cinv.issued"])
        return redirect(url_for("invoicing.invoice_detail", iid=inv.id))

    lang = current_lang()
    default_subject = (t["cinv.subject_default"] % days_label(start, end, lang))
    return render_template("invoice_new.html", clients=clients, client=client, month=month,
                           period_start=start, period_end=end,
                           cur=cur, currencies=currencies, lines=lines, total=total, any_missing=any_missing,
                           existing=existing, default_subject=default_subject,
                           # whoever issues the bill is its contact, unless they say otherwise
                           default_contact_name=current_user.full_name,
                           default_contact_phone=current_user.phone or current_user.email or "",
                           ledger_accounts=revenue_accounts(),
                           today=date.today().isoformat())


@invoicing_bp.route("/facturation/factures/<int:iid>")
@login_required
@require_perm("invoicing.view")
def invoice_detail(iid):
    inv = db.session.get(ClientInvoice, iid)
    if not inv:
        abort(404)
    # The lines are all coded alike; the page shows the one code they share.
    code = next((l.ledger_code for l in inv.lines if l.ledger_code), None)
    # A month billed reads as the month, as it always did; only a bill that
    # names other days shows them.
    full_month = not inv.period_start or (inv.period_start, inv.period_end) == month_bounds(inv.period)
    return render_template("client_invoice.html", inv=inv, today=date.today().isoformat(),
                           period_days=None if full_month else (inv.period_start, inv.period_end),
                           ledger_code=code, ledger_label=labels_for([code]).get(code))


@invoicing_bp.route("/facturation/factures/<int:iid>/annuler", methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def invoice_cancel(iid):
    inv = db.session.get(ClientInvoice, iid)
    if not inv:
        abort(404)
    if not inv.is_cancelled:
        inv.status = "cancelled"
        log_action("CANCEL", "client_invoice", resource_id=inv.id,
                   detail="Cancelled %s (%s, %s)" % (inv.number, inv.client.name, inv.period))
        db.session.commit()
        flash("success|" + get_t()["cinv.cancelled"])
    return redirect(url_for("invoicing.invoice_detail", iid=inv.id))


# ── Clients ──────────────────────────────────────────────────────────────────

def _clients_url():
    return url_for("client_reports.clients")


def _next_code():
    """The next free number: CL-004 after CL-003. Read off the codes in use,
    not kept in a counter, so nothing drifts; the unique constraint catches a
    tie and the save tries again."""
    with db.session.no_autoflush:
        taken = [c[0] for c in db.session.query(Client.code)
                 .filter(Client.code.like(CODE_PREFIX + "%")).all()]
    highest = 0
    for c in taken:
        try:
            highest = max(highest, int(c[len(CODE_PREFIX):]))
        except ValueError:
            pass
    return "%s%03d" % (CODE_PREFIX, highest + 1)


def _pickable_machines():
    """Machines a client can be given: the live ones in the user's fleets,
    each with whoever holds it now so the form can say so."""
    q = Vehicle.query.filter(Vehicle.deleted_at.is_(None))
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


@invoicing_bp.route("/facturation/clients/<int:cid>")
@login_required
@require_perm("invoicing.view")
def client_detail(cid):
    row = db.session.get(Client, cid)
    if not row:
        abort(404)
    book = billing.rate_book(row.id)
    today = date.today().isoformat()
    tag_names = {tg.id: tg.name for tg in BillingTag.query.all()}
    machines = []
    for v in row.live_machines:
        hist = list(reversed(book["tags"].get(v.id, [])))   # newest first
        cur = billing.tag_on(book, v.id, today)
        basis, currency, rate = billing.price_on(book, v.id, today)
        machines.append({"v": v, "rate": rate, "basis": basis, "cur": currency,
                         "tag": tag_names.get(cur),
                         "since": next((eff for eff, _ in hist), None),
                         "history": [(eff, tag_names.get(tid)) for eff, tid in hist]})
    # The money side, the mirror of a supplier's page.
    from blueprints import client_reports as cr
    date_from, date_to = cr.year_bounds()
    rows = cr.statement(row)
    to_collect = cr.open_bills(row.id)
    tab = request.args.get("tab")
    if tab not in ("a_encaisser", "releve", "machines"):
        tab = "a_encaisser" if to_collect else "releve"
    return render_template("client_detail.html", client=row, machines=machines,
                           opening=_client_opening(row.id), tab=tab, today=today,
                           rows=list(reversed(rows)), open_bills=to_collect,
                           date_from=date_from, date_to=date_to,
                           f=cr.figures(row, date_from, date_to, today))


def _clean_rate(raw):
    return (raw or "").replace(" ", "").replace(" ", "").replace(" ", "").strip()


def _get_client_or_404(cid):
    row = db.session.get(Client, cid)
    if not row:
        abort(404)
    return row


def _machine_tab(row):
    return url_for("invoicing.client_detail", cid=row.id, tab="machines")


@invoicing_bp.route("/facturation/clients/<int:cid>/machines/ajouter", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def client_machine(cid):
    """Give the client one machine with its tarif and the date it starts,
    or, with ?vid= for a machine it already has, a new tarif from a date. A
    machine held by another client moves here; its dated tarifs stay, so the
    months it worked elsewhere still bill as they were."""
    row = _get_client_or_404(cid)
    t = get_t()
    pickable = _pickable_machines()
    by_id = {v.id: v for v in pickable}
    fixed = by_id.get(request.args.get("vid", type=int))
    if fixed is not None and fixed.client_id != row.id:
        fixed = None
    book = billing.rate_book(row.id)
    today = date.today().isoformat()
    tags = _pickable_tags(book)
    error = None
    if request.method == "POST":
        v = fixed or by_id.get(request.form.get("vehicle_id", type=int))
        tag = db.session.get(BillingTag, request.form.get("tag_id", type=int) or 0)
        eff = (request.form.get("effective_from") or "").strip()
        if v is None:
            error = t["client.err.machine"]
        elif tag is None or not tag.is_active:
            error = t["client.err.tag"]
        elif not _valid_date(eff):
            error = t["client.err.date"]
        else:
            moved = v.client_id != row.id
            v.client_id = row.id
            if tag.id != billing.tag_on(book, v.id, eff):
                db.session.add(VehicleTag(vehicle_id=v.id, tag_id=tag.id,
                                          effective_from=eff, created_by=current_user.id))
            log_action("UPDATE", "client", resource_id=row.id,
                       detail="%s %s for client '%s' on tarif '%s' from %s"
                              % ("Added" if moved else "Retagged", v.code, row.name, tag.name, eff))
            db.session.commit()
            flash("success|" + t["client.machine_saved"])
            return modal_ok() if is_modal_request() else redirect(_machine_tab(row))
    choices = [v for v in pickable if v.client_id != row.id]
    tpl = "_client_machine_form.html" if is_modal_request() else "client_machine_form.html"
    status = 422 if (error and is_modal_request()) else 200
    cur_tag = billing.tag_on(book, fixed.id, today) if fixed else None
    basis_now, cur_now, rate_now = (billing.price_on(book, fixed.id, today)
                                    if fixed else (None, None, None))
    return render_template(tpl, client=row, fixed=fixed, choices=choices, error=error,
                           tags=tags, tag_now=next((n for i, n in tags if i == cur_tag), None),
                           rate_now=rate_now, basis_now=basis_now, cur_now=cur_now,
                           today=today), status


@invoicing_bp.route("/facturation/clients/<int:cid>/machines/<int:vid>/retirer", methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def client_machine_remove(cid, vid):
    """Take a machine off the client. Its past prices stay, so the months it
    worked here still bill the same."""
    row = _get_client_or_404(cid)
    v = db.session.get(Vehicle, vid)
    if v is not None and v.client_id == row.id and v in _pickable_machines():
        v.client_id = None
        log_action("UPDATE", "client", resource_id=row.id,
                   detail="Removed %s from client '%s'" % (v.code, row.name))
        db.session.commit()
        flash("success|" + get_t()["client.machine_removed"])
    return redirect(_machine_tab(row))


def _pickable_tags(book):
    """(id, "name — price") pairs of the live tarifs, for a select. The
    label carries the dollars and the per-month where they apply."""
    t = get_t()
    today = date.today().isoformat()
    out = []
    for tg in BillingTag.query.filter_by(is_active=True).order_by(BillingTag.name).all():
        rate = billing.latest(book["prices"].get(tg.id, []), today)
        if rate is None:
            out.append((tg.id, tg.name))
            continue
        label = "%s — %s" % (tg.name, _num(rate))
        if tg.currency != "GNF":
            label += " " + tg.currency
        if tg.basis == "month":
            label += "/" + t["lease.month"]
        out.append((tg.id, label))
    return out


@invoicing_bp.route("/facturation/tarifs")
@login_required
@require_perm("invoicing.view")
def tarifs():
    """The tarifs: each one's price today, since when, its past prices, and
    how many machines carry it. Prices are dated history — a past month
    bills at the price that applied then, whatever is typed here since."""
    show_archived = request.args.get("archives") == "1"
    rows = (BillingTag.query.filter(BillingTag.is_active.is_(not show_archived))
            .order_by(BillingTag.name).all())
    archived = BillingTag.query.filter_by(is_active=False).count()
    today = date.today().isoformat()
    prices = billing.price_book()
    # Machines carrying each tarif today, live ones only.
    counts, used = {}, set()
    live = {i for (i,) in Vehicle.query.filter(Vehicle.deleted_at.is_(None))
            .with_entities(Vehicle.id).all()}
    for vid, hist in billing.tag_book().items():
        used.update(tid for _, tid in hist)
        if vid not in live:
            continue
        cur = billing.latest(hist, today)
        if cur is not None:
            counts[cur] = counts.get(cur, 0) + 1
    data = []
    for tg in rows:
        hist = list(reversed(prices.get(tg.id, [])))   # newest first
        data.append({"tag": tg, "rate": billing.latest(prices.get(tg.id, []), today),
                     "since": next((eff for eff, _ in hist), None),
                     "history": hist, "count": counts.get(tg.id, 0),
                     "used": tg.id in used})
    return render_template("tarifs.html", rows=data, show_archived=show_archived,
                           archived=archived)


def _tarifs_url():
    return url_for("invoicing.tarifs")


def _save_tag_price(tag, raw, eff, t):
    """Append a dated price to the tarif when it differs from the one already
    in force that day. Returns an error text or None."""
    raw = _clean_rate(raw)
    if not raw.isdigit() or int(raw) <= 0:
        return t["client.err.rate"]
    if not _valid_date(eff):
        return t["client.err.date"]
    rate = int(raw)
    pairs = [(r.effective_from, r.rate_per_unit) for r in tag.prices]
    if rate != billing.latest(pairs, eff):
        db.session.add(BillingTagRate(tag_id=tag.id, rate_per_unit=rate,
                                      effective_from=eff, created_by=current_user.id))
    return None


@invoicing_bp.route("/facturation/tarifs/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def tarif_new():
    """A tarif is born with its name, its first price and the date that
    price starts — which may sit in the past, so months never billed yet
    bill at it."""
    t = get_t()
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        clash = BillingTag.query.filter(db.func.lower(BillingTag.name) == name.lower()).first()
        if not name:
            error = t.get("list.err.name_required", "Le nom est obligatoire.")
        elif clash:
            error = t["tarif.err.exists"]
        else:
            basis = request.form.get("basis")
            currency = request.form.get("tag_currency")
            tag = BillingTag(name=name, created_by=current_user.id,
                             basis=basis if basis in ("unit", "month") else "unit",
                             currency=currency if currency in ("GNF", "USD") else "GNF")
            db.session.add(tag)
            db.session.flush()
            error = _save_tag_price(tag, request.form.get("rate"),
                                    (request.form.get("effective_from") or "").strip(), t)
            if error is None:
                log_action("CREATE", "tarif", resource_id=tag.id,
                           detail="Created tarif '%s'" % tag.name)
                db.session.commit()
                flash("success|" + t["tarif.saved"])
                return modal_ok() if is_modal_request() else redirect(_tarifs_url())
            db.session.rollback()
    tpl = "_tarif_form.html" if is_modal_request() else "tarif_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=None, error=error, today=date.today().isoformat()), status


def _get_tag_or_404(tid):
    row = db.session.get(BillingTag, tid)
    if not row:
        abort(404)
    return row


@invoicing_bp.route("/facturation/tarifs/<int:tid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def tarif_edit(tid):
    """Rename only: the prices live in their own dated rows and a past month
    keeps billing on them whatever the tarif is called."""
    row = _get_tag_or_404(tid)
    t = get_t()
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        clash = (BillingTag.query.filter(db.func.lower(BillingTag.name) == name.lower())
                 .filter(BillingTag.id != row.id).first())
        if not name:
            error = t.get("list.err.name_required", "Le nom est obligatoire.")
        elif clash:
            error = t["tarif.err.exists"]
        else:
            row.name = name
            log_action("UPDATE", "tarif", resource_id=row.id,
                       detail="Renamed tarif to '%s'" % name)
            db.session.commit()
            flash("success|" + t["tarif.saved"])
            return modal_ok() if is_modal_request() else redirect(_tarifs_url())
    tpl = "_tarif_form.html" if is_modal_request() else "tarif_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error, today=date.today().isoformat()), status


@invoicing_bp.route("/facturation/tarifs/<int:tid>/prix", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def tarif_price(tid):
    """A new dated price on the tarif. The old rows stay: a month already
    billed moves nothing, and a month not yet billed takes, day by day, the
    price in force then."""
    row = _get_tag_or_404(tid)
    t = get_t()
    today = date.today().isoformat()
    pairs = [(r.effective_from, r.rate_per_unit) for r in row.prices]
    error = None
    if request.method == "POST":
        error = _save_tag_price(row, request.form.get("rate"),
                                (request.form.get("effective_from") or "").strip(), t)
        if error is None:
            log_action("UPDATE", "tarif", resource_id=row.id,
                       detail="Priced tarif '%s'" % row.name)
            db.session.commit()
            flash("success|" + t["tarif.saved"])
            return modal_ok() if is_modal_request() else redirect(_tarifs_url())
    tpl = "_tarif_price_form.html" if is_modal_request() else "tarif_price_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error, today=today,
                           rate_now=billing.latest(pairs, today)), status


@invoicing_bp.route("/facturation/tarifs/<int:tid>/archiver", methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def tarif_archive(tid):
    row = _get_tag_or_404(tid)
    row.is_active = not row.is_active
    log_action("UPDATE", "tarif", resource_id=row.id,
               detail="%s tarif '%s'" % ("Restored" if row.is_active else "Archived", row.name))
    db.session.commit()
    flash("success|" + get_t()["tarif.saved"])
    return redirect(_tarifs_url())


@invoicing_bp.route("/facturation/tarifs/<int:tid>/supprimer", methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def tarif_delete(tid):
    """Gone for good — but only a tarif no machine ever carried: one that
    was used holds the price of months already worked, so it archives."""
    row = _get_tag_or_404(tid)
    t = get_t()
    if VehicleTag.query.filter_by(tag_id=row.id).first() is not None:
        flash("error|" + t["tarif.in_use"])
        return redirect(_tarifs_url())
    log_action("DELETE", "tarif", resource_id=row.id, detail="Deleted tarif '%s'" % row.name)
    db.session.delete(row)
    db.session.commit()
    flash("success|" + t["tarif.deleted"])
    return redirect(_tarifs_url())


def _save_client(row):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = Client.query.filter(db.func.lower(Client.name) == name.lower())
    if row:
        clash = clash.filter(Client.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")

    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(Client.sort_order)).scalar() or 0) + 1
        row = Client(sort_order=nxt, code=_next_code())
        db.session.add(row)
    row.name = name
    for field in ("contact", "address", "email", "po_box", "attention", "tax_id", "rccm", "note"):
        setattr(row, field, (request.form.get(field) or "").strip() or None)
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "client", resource_id=row.id,
               detail="%s client '%s' (%s)"
                      % ("Created" if creating else "Updated", row.name, row.code))
    for attempt in range(3):
        try:
            db.session.commit()
            return None
        except IntegrityError:
            db.session.rollback()
            if not creating or attempt == 2:
                return t.get("list.err.name_taken", "Ce nom existe déjà.")
            row.code = _next_code()
            row = db.session.merge(row)
    return None


def _render_client_form(row, error=None):
    tpl = "_client_form.html" if is_modal_request() else "client_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


@invoicing_bp.route("/facturation/clients/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def client_new():
    if request.method == "POST":
        error = _save_client(None)
        if error:
            return _render_client_form(None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(_clients_url())
    return _render_client_form(None)


@invoicing_bp.route("/facturation/clients/<int:cid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def client_edit(cid):
    row = db.session.get(Client, cid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_client(row)
        if error:
            return _render_client_form(row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("invoicing.client_detail", cid=row.id))
    return _render_client_form(row)


@invoicing_bp.route("/facturation/clients/<int:cid>/<any(archive,reactivate,delete):what>",
                    methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def client_action(cid, what):
    """Archive takes it out of the pickers and keeps its history; delete is
    for one no invoice was ever issued to. Its prices go with it, its
    machines are set free, its account on the plan is hidden."""
    row = db.session.get(Client, cid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if row.has_history:
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(_clients_url())
        for v in row.machines:
            v.client_id = None
        if row.ledger_code:
            acc = LedgerAccount.query.filter_by(code=row.ledger_code, is_standard=False).first()
            if acc:
                acc.is_used = False
                acc.is_active = False
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "client", resource_id=cid, detail="Deleted client '%s'" % name)
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "client", resource_id=cid,
                   detail="%s client '%s'" % (what.title(), row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(_clients_url())


# ── Paramètres de facturation ────────────────────────────────────────────────
# What the printed bill says about the company, kept as app settings under
# their own category so the Administration page never lists them: they are
# the invoicing page's, like the sites are the cash box's.

SETTINGS_CATEGORY = "facturation"
SETTINGS_GROUPS = [
    ("company", [("company_name", False), ("company_address", True), ("company_phone", False),
                 ("company_email", False), ("company_website", False),
                 ("company_nif", False), ("company_rccm", False)]),
    ("payment", [("bank_details", True), ("payment_terms", True), ("tax_mention", False)]),
    ("footer",  [("invoice_title", False), ("invoice_footer", True)]),
]
SETTING_KEYS = [k for _, keys in SETTINGS_GROUPS for k, _ in keys]


def invoice_settings():
    """{key: value} for the printed bill, empty strings where nothing was set."""
    rows = {s.key: s.value for s in AppSetting.query
            .filter(AppSetting.key.in_(SETTING_KEYS)).all()}
    return {k: rows.get(k) or "" for k in SETTING_KEYS}


@invoicing_bp.route("/facturation/parametres", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def settings():
    if request.method == "POST":
        changed = 0
        for key in SETTING_KEYS:
            value = (request.form.get(key) or "").strip()
            row = db.session.get(AppSetting, key)
            if row is None:
                db.session.add(AppSetting(key=key, value=value, label=key,
                                          category=SETTINGS_CATEGORY, updated_by=current_user.id))
                changed += 1
            elif (row.value or "") != value:
                row.value = value
                row.updated_by = current_user.id
                changed += 1
        db.session.commit()
        log_action("UPDATE", "invoice_settings", detail="Invoice settings saved, %d changed" % changed)
        flash("success|" + get_t()["cset.saved"])
        return redirect(url_for("invoicing.settings"))
    return render_template("invoice_settings.html", groups=SETTINGS_GROUPS,
                           values=invoice_settings())


@invoicing_bp.route("/facturation/factures/<int:iid>/imprimer")
@login_required
@require_perm("invoicing.view")
def invoice_print(iid):
    """The bill as a sheet: standalone page that opens the print dialog, and
    "save as PDF" there makes the file to send."""
    inv = db.session.get(ClientInvoice, iid)
    if not inv:
        abort(404)
    lang = current_lang()
    currency = _get_setting("currency", "GNF")
    return render_template("invoice_print.html", inv=inv, co=invoice_settings(),
                           month_words=period_label(inv, lang),
                           amount_words=amount_in_words(inv.total, lang, currency))


# ── Règlements: what the client paid against a bill ──────────────────────────

def _get_client_invoice_or_404(iid):
    inv = db.session.get(ClientInvoice, iid)
    if not inv:
        abort(404)
    return inv


def _get_client_payment_or_404(iid, pid):
    pay = db.session.get(ClientPayment, pid)
    if not pay or pay.invoice_id != iid:
        abort(404)
    return pay


def _render_payment_form(inv, pay, error=None):
    tpl = "_client_payment_form.html" if is_modal_request() else "client_payment_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, invoice=inv, payment=pay, error=error,
                           payment_methods=PAYMENT_METHODS, accounts=active_accounts(),
                           today=date.today().isoformat()), status


def _read_payment_form(inv, pay, form=None):
    """One payment: when, how much, how. Returns (data, None) or (None, err).
    The amount is held to what is still owed, counting every other payment
    but this one, so correcting one downwards is never blocked by itself."""
    t = get_t()
    form = request.form if form is None else form
    amount = parse_money(form.get("amount"))
    if amount is None or amount <= 0:
        return None, t["invoice.err.amount"]
    others = sum(p.amount or 0 for p in inv.payments if pay is None or p.id != pay.id)
    if amount > (inv.total or 0) - others:
        return None, t["cpay.err.overpaid"]
    date_str = (form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["invoice.err.paid_date"]
    method = (form.get("method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, t["invoice.err.method"]
    account_id = form.get("account_id", type=int) or None
    if account_id and not CashAccount.query.filter_by(id=account_id, is_active=True).first():
        return None, t.get("caisse.err.unknown_account", "Choisissez un compte actif.")
    # Money that did not come in cash landed on an account, and that
    # account's balance counts on knowing which. Cash may go to the box,
    # which is not an account here.
    if method != "cash" and not account_id:
        return None, t["cpay.err.account"]
    err = method_error(db.session.get(CashAccount, account_id) if account_id else None, method)
    if err:
        return None, err
    return dict(date=date_str, amount=amount, method=method, account_id=account_id,
                reference=(form.get("reference") or "").strip()[:60] or None,
                note=(form.get("note") or "").strip()[:255] or None), None


@invoicing_bp.route("/facturation/factures/<int:iid>/reglements/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def payment_new(iid):
    inv = _get_client_invoice_or_404(iid)
    t = get_t()
    if inv.is_cancelled or inv.remaining <= 0:
        flash("error|" + t["cpay.err.closed"])
        return redirect(url_for("invoicing.invoice_detail", iid=inv.id))
    if request.method == "POST":
        data, error = _read_payment_form(inv, None)
        if error:
            return _render_payment_form(inv, None, error)
        pay = ClientPayment(invoice_id=inv.id, created_by=current_user.id, **data)
        # A receipt clears the client's account, never the revenue.
        pay.ledger_code = ensure_client_account(inv.client, current_user.id)
        db.session.add(pay)
        log_action("CREATE", "client_payment", resource_id=inv.id,
                   detail="Received %s GNF on %s" % (data["amount"], inv.number))
        db.session.commit()
        flash("success|" + t["cpay.saved"])
        return modal_ok() if is_modal_request() else redirect(url_for("invoicing.invoice_detail", iid=inv.id))
    return _render_payment_form(inv, None)


@invoicing_bp.route("/facturation/factures/<int:iid>/reglements/<int:pid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def payment_edit(iid, pid):
    inv = _get_client_invoice_or_404(iid)
    pay = _get_client_payment_or_404(iid, pid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_payment_form(inv, pay)
        if error:
            return _render_payment_form(inv, pay, error)
        for k, val in data.items():
            setattr(pay, k, val)
        if not pay.ledger_code:
            pay.ledger_code = ensure_client_account(inv.client, current_user.id)
        log_action("UPDATE", "client_payment", resource_id=pay.id,
                   detail="Edited payment #%s on %s" % (pay.id, inv.number))
        db.session.commit()
        flash("success|" + t["cpay.saved"])
        return modal_ok() if is_modal_request() else redirect(url_for("invoicing.invoice_detail", iid=inv.id))
    return _render_payment_form(inv, pay)


@invoicing_bp.route("/facturation/factures/<int:iid>/reglements/<int:pid>/supprimer", methods=["POST"])
@login_required
@require_perm("invoicing.manage")
def payment_delete(iid, pid):
    inv = _get_client_invoice_or_404(iid)
    pay = _get_client_payment_or_404(iid, pid)
    db.session.delete(pay)
    log_action("DELETE", "client_payment", resource_id=pid,
               detail="Deleted payment #%s on %s" % (pid, inv.number))
    db.session.commit()
    flash("success|" + get_t()["cpay.deleted"])
    return redirect(url_for("invoicing.invoice_detail", iid=inv.id))


@invoicing_bp.route("/facturation/factures/<int:iid>/supprimer", methods=["POST"])
@login_required
def invoice_delete(iid):
    """Gone for good, super admins only: a bill issued by mistake or as a
    trial. One that has taken money is not deleted -- its payments go first,
    one by one, so nothing is ever removed by accident behind a bill. Its
    number is free again, so the next bill may take it."""
    if not current_user.is_super_admin:
        abort(403)
    inv = _get_client_invoice_or_404(iid)
    t = get_t()
    if inv.payments:
        flash("error|" + t["cinv.err.delete_paid"])
        return redirect(url_for("invoicing.invoice_detail", iid=inv.id))
    number, client_name, period = inv.number, inv.client.name, inv.period
    db.session.delete(inv)
    log_action("DELETE", "client_invoice", resource_id=iid,
               detail="Deleted %s (%s, %s)" % (number, client_name, period))
    db.session.commit()
    flash("success|" + t["cinv.deleted"])
    return redirect(url_for("invoicing.index", tab="factures"))


# ── What a client owed before the app ───────────────────────────────────────

def _field_amount(value):
    """A stored amount as a whole-franc form field takes it: no ".00"."""
    if value is None:
        return None
    return int(value) if value == int(value) else value


def _client_opening(client_id):
    return ClientInvoice.query.filter_by(client_id=client_id, is_opening=True).first()


@invoicing_bp.route("/facturation/clients/<int:cid>/solde-anterieur", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def client_opening(cid):
    """One amount and the day it stood on: what the client owed then, from
    the old books. Held as a bill with no lines, so it is collected with the
    usual "Règlement" and counts in what is owed. Emptied before anything
    was paid on it, it goes away; once paid on, never below what was paid."""
    client = db.session.get(Client, cid)
    if not client:
        abort(404)
    t = get_t()
    row = _client_opening(cid)
    error = None
    if request.method == "POST":
        raw = (request.form.get("amount") or "").strip()
        amount = parse_amount(raw) if raw else 0
        day = (request.form.get("date") or "").strip()
        paid = row.paid_amount if row else 0
        if amount is None or amount < 0:
            error = t["invoice.err.amount"]
        elif not _valid_date(day):
            error = t["opening.err.date"]
        elif amount < paid:
            error = t["opening.err.below_paid"] % {"paid": _num(paid)}
        elif amount == 0 and row is None:
            return modal_ok() if is_modal_request() else redirect(url_for("invoicing.client_detail", cid=cid))
        if not error:
            if amount == 0 and not row.payments:
                db.session.delete(row)
                action = "DELETE"
            else:
                if row is None:
                    row = ClientInvoice(client_id=cid, is_opening=True, status="issued",
                                        number=("SA-" + client.code)[:20], created_by=current_user.id)
                    db.session.add(row)
                row.date, row.period, row.total = day, day[:7], amount
                row.subject = t["opening.subject"]
                action = "UPDATE"
            log_action(action, "client_opening", resource_id=cid,
                       detail="Opening balance of client '%s': %s GNF on %s" % (client.name, amount, day))
            db.session.commit()
            flash("success|" + t["opening.saved"])
            return modal_ok() if is_modal_request() else redirect(url_for("invoicing.client_detail", cid=cid))
    tpl = "_opening_form.html" if is_modal_request() else "opening_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error, today=date.today().isoformat(),
                           action=url_for("invoicing.client_opening", cid=cid),
                           back=url_for("invoicing.client_detail", cid=cid),
                           amount=_field_amount(row.total) if row else None,
                           paid=row.paid_amount if row else 0,
                           title=t["opening.client_title"]), status


# ── Receiving clients' payments on an account's page ────────────────────────

def _render_account_receipt_form(acc, error=None):
    """The client bills still owed, grouped by client, to tick the ones a
    single transfer pays."""
    tpl = "_account_client_receipt_form.html" if is_modal_request() else "account_client_receipt_form.html"
    status = 422 if (error and is_modal_request()) else 200
    bills = [i for i in ClientInvoice.query.filter(ClientInvoice.status != "cancelled").all() if i.remaining > 0]
    bills.sort(key=lambda i: ((i.client.name if i.client else "").lower(), i.due_date or i.date, i.date, i.id))
    groups = []
    for b in bills:
        if not groups or groups[-1]["client"] is not b.client:
            groups.append({"client": b.client, "bills": [], "total": 0})
        groups[-1]["bills"].append(b)
        groups[-1]["total"] += b.remaining
    ticked = {int(x) for x in request.form.getlist("invoice_ids") if x.isdigit()}
    return render_template(tpl, acc=acc, groups=groups, ticked=ticked, error=error,
                           payment_methods=methods_of(acc, PAYMENT_METHODS),
                           today=date.today().isoformat()), status


@invoicing_bp.route("/comptes/<int:aid>/recevoir-paiement", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.manage")
def account_receipt(aid):
    """A client's transfer arriving on this account, paying one or several
    of their bills. Each bill gets its own receipt -- the same as on the
    bill's page -- sharing a mark when there are several, so the account
    shows the one line the statement shows. Left empty, the amount settles
    every ticked bill; a smaller one is shared in proportion to what each
    still owes, never more than a bill owes."""
    from blueprints.supplier_invoices import spread
    acc = db.session.get(CashAccount, aid)
    if not acc or acc.is_repayable:
        abort(404)
    t = get_t()
    if request.method == "POST":
        ids = [int(x) for x in request.form.getlist("invoice_ids") if x.isdigit()]
        bills = [b for b in (db.session.get(ClientInvoice, i) for i in ids) if b is not None]
        if not bills or any(b.is_cancelled or b.remaining <= 0 for b in bills):
            return _render_account_receipt_form(acc, t["receipt.err.bill"])
        if len({b.client_id for b in bills}) > 1:
            return _render_account_receipt_form(acc, t["receipt.err.one_client"])
        bills.sort(key=lambda b: (b.due_date or b.date, b.date, b.id))
        owed = sum(b.remaining for b in bills)
        raw = (request.form.get("amount") or "").strip()
        if raw:
            total = parse_money(raw)
            if total is None or total <= 0:
                return _render_account_receipt_form(acc, t["invoice.err.amount"])
            if total > owed:
                return _render_account_receipt_form(acc, t["receipt.err.over"] % {"owed": _num(owed)})
        else:
            total = owed
        batch = uuid.uuid4().hex if len(bills) > 1 else None
        made = []
        # Shared out in cents, so a bill with cents is settled to the cent.
        in_cents = spread(int(round(Decimal(total) * 100)), [int(round(Decimal(b.remaining) * 100)) for b in bills])
        for b, part in zip(bills, (Decimal(c) / 100 for c in in_cents)):
            if part <= 0:
                continue
            form = request.form.copy()
            form["account_id"], form["amount"] = str(acc.id), str(part)
            data, error = _read_payment_form(b, None, form)
            if error:
                db.session.rollback()
                return _render_account_receipt_form(acc, error)
            pay = ClientPayment(invoice_id=b.id, created_by=current_user.id, batch=batch, **data)
            pay.ledger_code = ensure_client_account(b.client, current_user.id)
            db.session.add(pay)
            made.append((b.number, part))
        log_action("CREATE", "client_payment", resource_id=bills[0].client_id,
                   detail="Received %s GNF on account #%s for %s" % (
                       total, acc.id, ", ".join("%s: %s" % m for m in made)))
        db.session.commit()
        flash("success|" + (t["receipt.saved_many"] % {"n": len(made)} if len(made) > 1 else t["cpay.saved"]))
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=acc.id))
    return _render_account_receipt_form(acc)
