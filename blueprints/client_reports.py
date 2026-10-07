"""What the clients owe the company, told two ways -- the mirror of the
suppliers' side.

One client: the money on their page -- owed and late now, billed and
received over a period, the bills still to collect, and their statement:
the old balance, every bill (+) and every payment received (-), with what
is owed after each line. One transfer that paid several bills is one line.
It prints, to send when chasing a payment.

Several clients: the aged receivables -- for each, what is owed split by
how late it is. A bill with no due date falls due the day it is dated.
A cancelled bill counts for nothing anywhere here.
"""
from datetime import date, datetime

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import login_required

from app import get_t, require_perm
from models import Client, ClientInvoice, ClientPayment, db

client_reports_bp = Blueprint("client_reports", __name__)

BUCKETS = (("current", None, 0), ("d30", 1, 30), ("d60", 31, 60), ("d90", 61, 90), ("d90p", 91, None))


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def year_bounds():
    """The window the totals on a client's page cover: what the query asks
    for, else the current year."""
    today = date.today()
    df = (request.args.get("date_from") or "").strip()
    dt = (request.args.get("date_to") or "").strip()
    if "date_from" not in request.args and "date_to" not in request.args:
        df, dt = today.replace(month=1, day=1).isoformat(), today.isoformat()
    return (df if _valid_date(df) else ""), (dt if _valid_date(dt) else "")


def _bills(client_id):
    return (ClientInvoice.query.filter(ClientInvoice.client_id == client_id,
                                       ClientInvoice.status != "cancelled").all())


def statement(client):
    """Every bill and every payment received from one client, the old
    balance first, then oldest first, each with what is owed after it."""
    t = get_t()
    bills = _bills(client.id)
    rows = []
    for b in bills:
        rows.append(dict(kind="opening" if b.is_opening else "bill", date=b.date, at=b.id, bill=b,
                         label=b.number, detail=(b.subject or "") if not b.is_opening else "",
                         debit=b.total or 0, credit=0))
    ids = [b.id for b in bills]
    payments = ClientPayment.query.filter(ClientPayment.invoice_id.in_(ids)).all() if ids else []
    groups = {}
    for p in payments:
        groups.setdefault(p.batch or ("one", p.id), []).append(p)
    for parts in groups.values():
        first = parts[0]
        where = first.account.name if first.account else t["client.received_cash"]
        rows.append(dict(kind="payment", date=first.date, at=first.id, bill=first.invoice,
                         label=", ".join(p.invoice.number for p in parts if p.invoice),
                         detail=where + (" · " + first.reference if first.reference else ""),
                         debit=0, credit=sum(p.amount or 0 for p in parts)))
    order = {"opening": 0, "bill": 1, "payment": 2}
    rows.sort(key=lambda r: (r["kind"] != "opening", r["date"], order[r["kind"]], r["at"]))
    owed = 0
    for r in rows:
        owed += r["debit"] - r["credit"]
        r["balance"] = owed
    return rows


def figures(client, date_from, date_to, today):
    """Owed and late now; billed and received over the period."""
    bills = _bills(client.id)
    within = lambda d: (not date_from or d >= date_from) and (not date_to or d <= date_to)
    return dict(
        owed=sum(b.remaining for b in bills),
        late=sum(b.remaining for b in bills if b.remaining > 0 and (b.due_date or b.date) < today),
        billed=sum(b.total or 0 for b in bills if not b.is_opening and within(b.date)),
        received=sum(p.amount or 0 for b in bills for p in b.payments if within(p.date)))


def open_bills(client_id):
    return sorted((b for b in _bills(client_id) if b.remaining > 0),
                  key=lambda b: (b.due_date or b.date, b.date))


def _owed_by_client():
    """What each client still owes, and how many bills, in one pass."""
    out = {}
    for b in ClientInvoice.query.filter(ClientInvoice.status != "cancelled").all():
        e = out.setdefault(b.client_id, {"count": 0, "remaining": 0})
        e["count"] += 1
        e["remaining"] += max(b.remaining, 0)
    return out


@client_reports_bp.route("/clients")
@login_required
@require_perm("invoicing.view")
def clients():
    """The clients, on a page of their own: who the machines work for and
    what each still owes. A name opens the client's page."""
    show_archived = request.args.get("archives") == "1"
    q = Client.query.order_by(Client.sort_order, Client.name)
    if not show_archived:
        q = q.filter(Client.is_active.is_(True))
    return render_template("clients.html", clients=q.all(), owed=_owed_by_client(),
                           show_archived=show_archived,
                           archived=Client.query.filter(Client.is_active.is_(False)).count())


@client_reports_bp.route("/clients/<int:cid>/releve.print")
@login_required
@require_perm("invoicing.view")
def statement_print(cid):
    client = db.session.get(Client, cid)
    if not client:
        abort(404)
    today = date.today().isoformat()
    return render_template("client_statement_print.html", client=client, rows=statement(client), today=today,
                           f=figures(client, "", "", today),
                           back_url=url_for("invoicing.client_detail", cid=cid),
                           generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))


def aged(client_ids=None):
    """For each client owing something, what is owed split by lateness,
    biggest first, and the totals of each column."""
    today = date.today()
    q = ClientInvoice.query.filter(ClientInvoice.status != "cancelled")
    if client_ids:
        q = q.filter(ClientInvoice.client_id.in_(client_ids))
    per = {}
    for b in q.all():
        left = b.remaining
        if left <= 0:
            continue
        late = (today - datetime.strptime(b.due_date or b.date, "%Y-%m-%d").date()).days
        col = next(name for name, lo, hi in BUCKETS
                   if (lo is None and late <= hi) or (hi is None and late >= lo)
                   or (lo is not None and hi is not None and lo <= late <= hi))
        row = per.setdefault(b.client_id, dict(who=b.client, total=0, bills=0,
                                               **{name: 0 for name, _, _ in BUCKETS}))
        row[col] += left
        row["total"] += left
        row["bills"] += 1
    rows = sorted(per.values(), key=lambda r: -r["total"])
    totals = {name: sum(r[name] for r in rows) for name, _, _ in BUCKETS}
    totals["total"] = sum(r["total"] for r in rows)
    return rows, totals


@client_reports_bp.route("/clients/etat")
@login_required
@require_perm("invoicing.view")
def aged_report():
    ids = [int(x) for x in request.args.getlist("client") if x.isdigit()]
    rows, totals = aged(ids)
    printing = request.args.get("print") == "1"
    args = request.args.to_dict(flat=False)
    args.pop("print", None)
    return render_template("client_aged_print.html" if printing else "client_aged.html",
                           rows=rows, totals=totals, buckets=[b[0] for b in BUCKETS],
                           clients=Client.query.order_by(Client.name).all(), client_ids=ids,
                           today=date.today().isoformat(),
                           print_url=url_for("client_reports.aged_report", print=1, **args),
                           back_url=url_for("client_reports.aged_report", **args),
                           generated=datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
