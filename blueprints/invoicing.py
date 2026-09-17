"""Facturation — the client side of the money, in two tabs.

Clients: who the machines work for. The mirror of the lessor side of
Fournisseurs: a supplier can lease machines to the company and the app knows
what each costs; a client has machines placed with it and the app knows what
each earns. One machine may be both at once -- leased from Diallo, working
for VIVO -- with a price on each side.

A client's machines are ticked on its form, like a lessor's. Each carries a
rate, GNF per worked unit, as dated history (see billing.py): saving a new
rate appends a row with its date of effect, the old one stays for the months
it applied. Codes are CL-001, CL-002... issued by the app, never typed.

Factures: for a client and a month, what its machines earned -- each entry's
units × the machine's rate on that day. Today this is a computation the page
does when asked; issuing it as a numbered invoice that no later correction
can move is the next step, and so are the payments received against it.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

import billing
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, require_perm)
from models import Client, ClientRate, DailyEntry, Vehicle, db

invoicing_bp = Blueprint("invoicing", __name__)

CODE_PREFIX = "CL-"
TABS = ("factures", "clients")


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


def _month_rows(client, month):
    """One row per machine that worked for the client that month: units,
    amount at the rate of each entry's day, and whether a day had no rate."""
    book = billing.rate_book(client.id)
    like = month + "%"
    month_end = month + "-31"
    rows, total, any_missing = [], 0, False
    fids = current_user_fleet_ids()
    for v in client.live_machines:
        if fids is not None and v.fleet_id not in fids:
            continue
        unit_type = v.category.unit_type
        entries = (DailyEntry.query.filter_by(vehicle_id=v.id)
                   .filter(DailyEntry.date.like(like)).all())
        units, amount, missing = 0.0, 0.0, False
        for e in entries:
            u = billing.entry_units(e, unit_type)
            if u <= 0:
                continue
            units += u
            rate = billing.rate_on(book, v.id, e.date)
            if rate is None:
                missing = True
            else:
                amount += u * rate
        if units > 0:
            rows.append({"v": v, "unit_type": unit_type, "units": units,
                         "amount": int(round(amount)), "missing": missing,
                         "ref_rate": billing.rate_on(book, v.id, month_end)})
            total += int(round(amount))
            any_missing = any_missing or missing
    rows.sort(key=lambda r: r["amount"], reverse=True)
    return rows, total, any_missing


@invoicing_bp.route("/facturation")
@login_required
@require_perm("invoicing.view")
def index():
    tab = request.args.get("tab")
    clients = _clients()
    if tab not in TABS:
        # Nobody to bill yet: the clients tab is where the work starts.
        tab = "clients" if not clients else "factures"
    show_archived = request.args.get("archives") == "1"
    if tab == "clients" and show_archived:
        clients = _clients(include_archived=True)
    archived = Client.query.filter(Client.is_active.is_(False)).count()

    month = request.args.get("month", "")
    if not _valid_month(month):
        month = datetime.utcnow().strftime("%Y-%m")
    client = None
    rows, total, any_missing = [], 0, False
    if tab == "factures" and clients:
        cid = request.args.get("client_id", type=int)
        client = next((c for c in clients if c.id == cid), None) or clients[0]
        rows, total, any_missing = _month_rows(client, month)

    tab_urls = {name: url_for("invoicing.index", tab=name) for name in TABS}
    return render_template("invoicing.html", tab=tab, tab_urls=tab_urls,
                           clients=clients, client=client, month=month,
                           rows=rows, total=total, any_missing=any_missing,
                           show_archived=show_archived, archived=archived)


# ── Clients ──────────────────────────────────────────────────────────────────

def _clients_url():
    return url_for("invoicing.index", tab="clients")


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
    machines = []
    for v in row.live_machines:
        hist = list(reversed(book.get(v.id, [])))   # newest first
        machines.append({"v": v, "rate": billing.rate_on(book, v.id, today),
                         "since": next((eff for eff, _ in hist), None),
                         "history": hist})
    return render_template("client_detail.html", client=row, machines=machines)


def _assign_machines(row, chosen_ids):
    """Make `chosen_ids` this client's machines, among those the user may
    touch. A machine picked away from another client moves; one the user
    cannot see is left exactly as it is, whoever holds it."""
    for v in _pickable_machines():
        if v.id in chosen_ids:
            v.client_id = row.id
        elif v.client_id == row.id:
            v.client_id = None


def _clean_rate(raw):
    return (raw or "").replace(" ", "").replace(" ", "").replace(" ", "").strip()


def _record_rates(row, chosen_ids, eff):
    """Append a dated rate for every chosen machine whose box holds a number
    different from the rate in force on `eff`. An empty box changes nothing;
    the same number changes nothing. Returns how many were written."""
    book = billing.rate_book(row.id)
    written = 0
    for vid in chosen_ids:
        raw = _clean_rate(request.form.get("rate_%d" % vid))
        if not raw:
            continue
        new_rate = int(raw)
        if new_rate == billing.rate_on(book, vid, eff):
            continue
        db.session.add(ClientRate(client_id=row.id, vehicle_id=vid, rate_per_unit=new_rate,
                                  effective_from=eff, created_by=current_user.id))
        written += 1
    return written


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
    eff = (request.form.get("rate_effective_from") or "").strip() or date.today().isoformat()
    if not _valid_date(eff):
        return t.get("client.err.date", "Date d'effet invalide.")
    chosen = set()
    for raw in request.form.getlist("machine_ids"):
        try:
            chosen.add(int(raw))
        except (TypeError, ValueError):
            pass
    for vid in chosen:
        r = _clean_rate(request.form.get("rate_%d" % vid))
        if r and not r.isdigit():
            return t.get("client.err.rate", "Un tarif est un nombre entier, en GNF.")

    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(Client.sort_order)).scalar() or 0) + 1
        row = Client(sort_order=nxt, code=_next_code())
        db.session.add(row)
    row.name = name
    for field in ("contact", "address", "tax_id", "rccm", "note"):
        setattr(row, field, (request.form.get(field) or "").strip() or None)
    db.session.flush()
    _assign_machines(row, chosen)
    rates = _record_rates(row, chosen, eff)
    log_action("CREATE" if creating else "UPDATE", "client", resource_id=row.id,
               detail="%s client '%s' (%s), %d machines, %d rate changes"
                      % ("Created" if creating else "Updated", row.name, row.code,
                         len(chosen), rates))
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
    rates = {}
    if row is not None:
        book = billing.rate_book(row.id)
        today = date.today().isoformat()
        rates = {vid: billing.rate_on(book, vid, today) for vid in book}
    return render_template(tpl, row=row, error=error, machines=_pickable_machines(),
                           rates=rates, today=date.today().isoformat()), status


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
    only for one nothing was ever priced for."""
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
