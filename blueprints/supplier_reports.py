"""What the company owes its suppliers, told two ways.

One supplier: their page, with their statement -- the old balance, every
bill (+) and every payment (-), and what is owed after each line, the
document to hold against the statement the supplier sends. One transfer that
paid several bills is one line, as on the bank's statement.

Several suppliers: the aged payables -- for each, what is owed, split by
how late it is. A bill with no due date falls due the day it is dated.
Both print, for the browser to save as a PDF.
"""
from datetime import date, datetime

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import login_required

from app import get_t, require_perm
from models import PurchaseOrder, Supplier, SupplierInvoice, SupplierPayment, db

supplier_reports_bp = Blueprint("supplier_reports", __name__)

# The lateness columns of the aged payables, in days past due.
BUCKETS = (("current", None, 0), ("d30", 1, 30), ("d60", 31, 60), ("d90", 61, 90), ("d90p", 91, None))


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _year_bounds():
    """The window the totals on a supplier's page cover: what the query asks
    for, else the current year."""
    today = date.today()
    df = (request.args.get("date_from") or "").strip()
    dt = (request.args.get("date_to") or "").strip()
    if "date_from" not in request.args and "date_to" not in request.args:
        df, dt = today.replace(month=1, day=1).isoformat(), today.isoformat()
    return (df if _valid_date(df) else ""), (dt if _valid_date(dt) else "")


def _source(p):
    """Where an instalment's money came from, in words."""
    t = get_t()
    if p.from_cash_box:
        return t["supplier.paid_from_till"]
    return p.account.name if p.account else t["supplier.paid_from_unknown"]


def statement(sup):
    """Every bill and every payment of one supplier, oldest first, each with
    what is owed after it."""
    t = get_t()
    bills = SupplierInvoice.query.filter_by(supplier_id=sup.id).all()
    rows = []
    for b in bills:
        rows.append(dict(kind="opening" if b.is_opening else "bill", date=b.date, at=b.id, bill=b,
                         label=t["opening.title"] if b.is_opening else (b.number or ""),
                         detail=b.description or "", debit=b.amount or 0, credit=0))
    payments = (SupplierPayment.query.join(SupplierInvoice)
                .filter(SupplierInvoice.supplier_id == sup.id).all())
    batches = {}
    for p in payments:
        if p.batch:
            batches.setdefault(p.batch, []).append(p)
        else:
            batches[("one", p.id)] = [p]
    for parts in batches.values():
        first = parts[0]
        rows.append(dict(kind="payment", date=first.date, at=first.id, bill=first.invoice,
                         label=", ".join((t["opening.title"] if p.invoice.is_opening else (p.invoice.number or p.invoice.date))
                                         for p in parts if p.invoice),
                         detail=_source(first) + (" · " + first.reference if first.reference else ""),
                         debit=0, credit=sum(p.amount or 0 for p in parts)))
    # The old balance first, whatever its date: it is everything before the
    # app. Then by date, a bill before the payment on it.
    order = {"opening": 0, "bill": 1, "payment": 2}
    rows.sort(key=lambda r: (r["kind"] != "opening", r["date"], order[r["kind"]], r["at"]))
    owed = 0
    for r in rows:
        owed += r["debit"] - r["credit"]
        r["balance"] = owed
    return rows


def figures(sup, date_from, date_to, today):
    """The numbers at the top of a supplier's page: owed now, late now, and
    bought and paid over the period."""
    bills = SupplierInvoice.query.filter_by(supplier_id=sup.id).all()
    within = lambda d: (not date_from or d >= date_from) and (not date_to or d <= date_to)
    owed = sum(b.remaining for b in bills)
    late = sum(b.remaining for b in bills if b.remaining > 0 and (b.due_date or b.date) < today)
    bought = sum(b.amount or 0 for b in bills if not b.is_opening and within(b.date))
    paid = sum(p.amount or 0 for b in bills for p in b.payments if within(p.date))
    return dict(owed=owed, late=late, bought=bought, paid=paid)


def _supplier_or_404(sid):
    sup = db.session.get(Supplier, sid)
    if not sup:
        abort(404)
    return sup


@supplier_reports_bp.route("/fournisseurs/<int:sid>")
@login_required
@require_perm("supplier_invoice.view")
def detail(sid):
    sup = _supplier_or_404(sid)
    today = date.today().isoformat()
    date_from, date_to = _year_bounds()
    rows = statement(sup)
    open_bills = sorted((b for b in SupplierInvoice.query.filter_by(supplier_id=sid).all() if b.remaining > 0),
                        key=lambda b: (b.due_date or b.date, b.date))
    orders = (PurchaseOrder.query.filter_by(supplier_id=sid)
              .order_by(PurchaseOrder.date.desc(), PurchaseOrder.id.desc()).limit(20).all())
    return render_template("supplier_detail.html", sup=sup, rows=list(reversed(rows)),
                           open_bills=open_bills, orders=orders, today=today,
                           date_from=date_from, date_to=date_to,
                           f=figures(sup, date_from, date_to, today))


@supplier_reports_bp.route("/fournisseurs/<int:sid>/releve.print")
@login_required
@require_perm("supplier_invoice.view")
def statement_print(sid):
    sup = _supplier_or_404(sid)
    today = date.today().isoformat()
    rows = statement(sup)
    return render_template("supplier_statement_print.html", sup=sup, rows=rows, today=today,
                           f=figures(sup, "", "", today),
                           back_url=url_for("supplier_reports.detail", sid=sid),
                           generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))


def aged(supplier_ids=None, kind=""):
    """For each supplier owed something, what is owed split by lateness,
    and the totals of each column."""
    today = date.today()
    q = SupplierInvoice.query.join(Supplier)
    if supplier_ids:
        q = q.filter(SupplierInvoice.supplier_id.in_(supplier_ids))
    if kind:
        q = q.filter(Supplier.kind == kind)
    per = {}
    for b in q.all():
        left = b.remaining
        if left <= 0:
            continue
        due = datetime.strptime(b.due_date or b.date, "%Y-%m-%d").date()
        late = (today - due).days
        col = next(name for name, lo, hi in BUCKETS
                   if (lo is None and late <= hi) or (hi is None and late >= lo)
                   or (lo is not None and hi is not None and lo <= late <= hi))
        row = per.setdefault(b.supplier_id, dict(sup=b.supplier, total=0, bills=0,
                                                 **{name: 0 for name, _, _ in BUCKETS}))
        row[col] += left
        row["total"] += left
        row["bills"] += 1
    rows = sorted(per.values(), key=lambda r: -r["total"])
    totals = {name: sum(r[name] for r in rows) for name, _, _ in BUCKETS}
    totals["total"] = sum(r["total"] for r in rows)
    return rows, totals


@supplier_reports_bp.route("/fournisseurs/etat")
@login_required
@require_perm("supplier_invoice.view")
def aged_report():
    ids = []
    for raw in request.args.getlist("supplier"):
        if raw.isdigit():
            ids.append(int(raw))
    kind = request.args.get("kind", "")
    if kind not in ("permanent", "divers"):
        kind = ""
    rows, totals = aged(ids, kind)
    suppliers = Supplier.query.order_by(Supplier.name).all()
    printing = request.args.get("print") == "1"
    args = request.args.to_dict(flat=False)
    args.pop("print", None)
    return render_template("supplier_aged_print.html" if printing else "supplier_aged.html",
                           rows=rows, totals=totals, buckets=[b[0] for b in BUCKETS],
                           suppliers=suppliers, supplier_ids=ids, kind=kind,
                           today=date.today().isoformat(),
                           print_url=url_for("supplier_reports.aged_report", print=1, **args),
                           back_url=url_for("supplier_reports.aged_report", **args),
                           generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
