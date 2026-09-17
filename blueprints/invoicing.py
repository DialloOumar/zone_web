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
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

import billing
from amount_words import amount_in_words
from app import (_get_setting, current_lang, current_user_fleet_ids, get_t, has_perm,
                 is_modal_request, log_action, modal_ok, parse_amount, require_perm)
from blueprints.expenses import PAYMENT_METHODS, active_accounts
from models import (AppSetting, CashAccount, Client, ClientInvoice, ClientInvoiceLine,
                    ClientPayment, ClientRate, DailyEntry, Vehicle, db)

invoicing_bp = Blueprint("invoicing", __name__)

CODE_PREFIX = "CL-"
TABS = ("factures", "clients")

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


def _month_lines(client, month):
    """The month worked out: one line per machine and rate in force, since a
    price that changed mid-month gives the same machine two lines. Returns
    (lines, total, any_missing); a line with rate None is one an entry had
    no price for, and blocks issuing."""
    book = billing.rate_book(client.id)
    like = month + "%"
    fids = current_user_fleet_ids()
    groups = {}
    for v in client.live_machines:
        if fids is not None and v.fleet_id not in fids:
            continue
        unit_type = v.category.unit_type
        entries = (DailyEntry.query.filter_by(vehicle_id=v.id)
                   .filter(DailyEntry.date.like(like)).order_by(DailyEntry.date).all())
        for e in entries:
            u = billing.entry_units(e, unit_type)
            if u <= 0:
                continue
            rate = billing.rate_on(book, v.id, e.date)
            g = groups.setdefault((v.code, rate), {
                "vehicle_id": v.id, "vehicle_code": v.code,
                "category_label": v.category.label_fr, "unit_type": unit_type,
                "units": 0.0, "rate": rate, "amount": 0})
            g["units"] += u
    lines = []
    for g in groups.values():
        g["amount"] = int(round(g["units"] * g["rate"])) if g["rate"] is not None else 0
        lines.append(g)
    lines.sort(key=lambda g: (g["vehicle_code"], g["rate"] or 0))
    total = sum(g["amount"] for g in lines)
    any_missing = any(g["rate"] is None for g in lines)
    return lines, total, any_missing


def _open_invoice(client_id, period):
    """The bill already issued for that client and month, if one stands."""
    return (ClientInvoice.query.filter_by(client_id=client_id, period=period)
            .filter(ClientInvoice.status != "cancelled").first())


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
    tab = request.args.get("tab")
    clients = _clients()
    if tab not in TABS:
        # Nobody to bill yet: the clients tab is where the work starts.
        tab = "clients" if not clients else "factures"
    show_archived = request.args.get("archives") == "1"
    if tab == "clients" and show_archived:
        clients = _clients(include_archived=True)
    archived = Client.query.filter(Client.is_active.is_(False)).count()

    today = date.today().isoformat()
    invoices, pagination = [], None
    billed = received = remaining = overdue_count = 0
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
        sums = live.with_entities(
            db.func.coalesce(db.func.sum(ClientInvoice.total), 0),
            db.func.coalesce(db.func.sum(db.func.min(paid, ClientInvoice.total)), 0)).one()
        billed, received = int(sums[0] or 0), int(sums[1] or 0)
        remaining = max(billed - received, 0)
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
        rows = (db.session.query(ClientInvoice.client_id, db.func.count(ClientInvoice.id),
                                 db.func.coalesce(db.func.sum(ClientInvoice.total - db.func.min(paid, ClientInvoice.total)), 0))
                .filter(ClientInvoice.status != "cancelled")
                .group_by(ClientInvoice.client_id).all())
        owed = {r[0]: {"count": int(r[1]), "remaining": int(r[2] or 0)} for r in rows}

    kept = request.args.to_dict(flat=False)
    kept.pop("tab", None)
    kept.pop("page", None)
    tab_urls = {name: url_for("invoicing.index", tab=name, **kept) for name in TABS}
    return render_template("invoicing.html", tab=tab, tab_urls=tab_urls,
                           clients=clients, invoices=invoices, pagination=pagination,
                           billed=billed, received=received, remaining=remaining,
                           overdue_count=overdue_count, owed=owed,
                           date_from=date_from, date_to=date_to, client_ids=client_ids,
                           status=status, search=search, today=today,
                           filtered=bool(date_from or date_to or client_ids or status or search),
                           show_archived=show_archived, archived=archived)


# ── Issuing a bill ───────────────────────────────────────────────────────────

@invoicing_bp.route("/facturation/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("invoicing.view")
def invoice_new():
    clients = _clients()
    if not clients:
        return redirect(url_for("invoicing.index", tab="clients"))
    cid = request.values.get("client_id", type=int)
    client = next((c for c in clients if c.id == cid), None) or clients[0]
    month = request.values.get("month", "")
    if not _valid_month(month):
        month = datetime.utcnow().strftime("%Y-%m")
    lines, total, any_missing = _month_lines(client, month)
    existing = _open_invoice(client.id, month)
    t = get_t()

    if request.method == "POST":
        if not has_perm("invoicing.manage"):
            abort(403)
        if existing:
            flash("error|" + t["cinv.already"] + " " + existing.number)
            return redirect(url_for("invoicing.invoice_detail", iid=existing.id))
        if not lines or any_missing:
            flash("error|" + (t["cinv.cannot_missing"] if any_missing else t["cinv.cannot_empty"]))
            return redirect(url_for("invoicing.invoice_new", client_id=client.id, month=month))
        issue_date = (request.form.get("date") or "").strip() or date.today().isoformat()
        due = (request.form.get("due_date") or "").strip() or None
        if not _valid_date(issue_date) or (due and not _valid_date(due)):
            flash("error|" + t.get("client.err.date", "Date invalide."))
            return redirect(url_for("invoicing.invoice_new", client_id=client.id, month=month))
        inv = ClientInvoice(client_id=client.id, number=_next_number(issue_date), period=month,
                            date=issue_date, due_date=due, total=total,
                            subject=(request.form.get("subject") or "").strip()[:120] or None,
                            client_ref=(request.form.get("client_ref") or "").strip()[:60] or None,
                            contact_name=(request.form.get("contact_name") or "").strip()[:120] or None,
                            contact_phone=(request.form.get("contact_phone") or "").strip()[:60] or None,
                            note=(request.form.get("note") or "").strip() or None,
                            created_by=current_user.id)
        for g in lines:
            inv.lines.append(ClientInvoiceLine(
                vehicle_id=g["vehicle_id"], vehicle_code=g["vehicle_code"],
                category_label=g["category_label"], unit_type=g["unit_type"],
                units=g["units"], rate=g["rate"], amount=g["amount"]))
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
        log_action("CREATE", "client_invoice", resource_id=inv.id,
                   detail="Issued %s to %s for %s: %d GNF" % (inv.number, client.name, month, inv.total))
        db.session.commit()
        flash("success|" + t["cinv.issued"])
        return redirect(url_for("invoicing.invoice_detail", iid=inv.id))

    lang = current_lang()
    default_subject = (t["cinv.subject_default"] % month_label(month, lang))
    return render_template("invoice_new.html", clients=clients, client=client, month=month,
                           lines=lines, total=total, any_missing=any_missing,
                           existing=existing, default_subject=default_subject,
                           # whoever issues the bill is its contact, unless they say otherwise
                           default_contact_name=current_user.full_name,
                           default_contact_phone=current_user.phone or current_user.email or "",
                           today=date.today().isoformat())


@invoicing_bp.route("/facturation/factures/<int:iid>")
@login_required
@require_perm("invoicing.view")
def invoice_detail(iid):
    inv = db.session.get(ClientInvoice, iid)
    if not inv:
        abort(404)
    return render_template("client_invoice.html", inv=inv, today=date.today().isoformat())


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
    ("footer",  [("invoice_footer", True)]),
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
                           month_words=month_label(inv.period, lang),
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


def _read_payment_form(inv, pay):
    """One payment: when, how much, how. Returns (data, None) or (None, err).
    The amount is held to what is still owed, counting every other payment
    but this one, so correcting one downwards is never blocked by itself."""
    t = get_t()
    amount = parse_amount(request.form.get("amount"))
    if amount is None or amount <= 0:
        return None, t["invoice.err.amount"]
    others = sum(p.amount or 0 for p in inv.payments if pay is None or p.id != pay.id)
    if amount > (inv.total or 0) - others:
        return None, t["cpay.err.overpaid"]
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["invoice.err.paid_date"]
    method = (request.form.get("method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, t["invoice.err.method"]
    account_id = request.form.get("account_id", type=int) or None
    if account_id and not CashAccount.query.filter_by(id=account_id, is_active=True).first():
        return None, t.get("caisse.err.unknown_account", "Choisissez un compte actif.")
    return dict(date=date_str, amount=amount, method=method, account_id=account_id,
                reference=(request.form.get("reference") or "").strip()[:60] or None,
                note=(request.form.get("note") or "").strip()[:255] or None), None


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
