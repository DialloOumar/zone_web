"""Factures fournisseurs — the bills the company has received and owes.

The opposite direction from the facturation screen, which bills clients: here
a garage, a parts shop or a landlord sends us a paper, it is recorded with its
amount and its due date, and the page says what is still owed and to whom.

It is a register, not a till. Nothing written here touches the cash box or the
expense ledger: the money actually leaving is logged as an expense like any
other, and an invoice counted in both places would be the same money twice.

A bill is settled in instalments, each with its own date and method. Where the
money came from decides where the instalment is entered, and the two never
overlap: the cash box's payments are entered on the Dépenses page and write
their instalment beside the cost, everyone else's are entered here. So the
money that left the box is described in exactly one place and can never be
counted twice.

Nothing about a payment is stored that can be worked out. Paid, part-paid and
untouched all follow from the instalments, so a status can never drift away
from the figures it is supposed to describe.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

import s3_storage
from app import (current_user_fleet_ids, get_t, has_perm, is_modal_request, log_action,
                 modal_ok, parse_amount, require_any_perm, require_perm)
# The one list of ways money changes hands, shared with the cash box and every
# other screen that records a payment, so a method added there shows up here.
from blueprints.expenses import PAYMENT_METHODS, active_accounts
from models import (CashAccount, PurchaseOrder, Supplier, SupplierInvoice, SupplierPayment,
                    Vehicle, db)

supplier_invoices_bp = Blueprint("supplier_invoices", __name__)

# The two halves of the page, shown one at a time: the bills, and the people
# who send them.
TABS = ("factures", "fournisseurs")

# The two kinds of supplier, and the series each one's code is drawn from.
SUPPLIER_KINDS = ("permanent", "divers")
CODE_PREFIX = {"permanent": "FP", "divers": "FD"}

# What the list can be narrowed to. "due" is everything with money still on it;
# "overdue" is the part of it that is already late.
STATUSES = ("due", "overdue", "paid")

PER_PAGE = 50

_PHOTO_ERR_KEYS = {
    s3_storage.ERR_TOO_LARGE:      "photo.err.too_large",
    s3_storage.ERR_BAD_FORMAT:     "photo.err.bad_format",
    s3_storage.ERR_NOT_CONFIGURED: "photo.err.not_configured",
    s3_storage.ERR_S3:             "photo.err.s3",
    s3_storage.ERR_UNKNOWN:        "photo.err.unknown",
    s3_storage.ERR_TOO_MANY_PAGES: "scan.err.too_many_pages",
    s3_storage.ERR_NOT_PDF:        "scan.err.not_pdf",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _by_kind_then_name():
    """The habitual suppliers first, then the occasional ones, each in name
    order: what someone reaching for a picker wants under the hand."""
    return (db.case((Supplier.kind == "permanent", 0), else_=1), Supplier.name)


def active_suppliers():
    """Who a new invoice can be filed under. An archived supplier keeps its
    invoices and their history, it is simply no longer offered."""
    return (Supplier.query.filter(Supplier.is_active.is_(True))
            .order_by(*_by_kind_then_name()).all())


def parts_suppliers():
    """Who a bon de commande can be addressed to: the active suppliers that
    sell parts. A lessor that sells none is not offered to the store."""
    return (Supplier.query.filter(Supplier.is_active.is_(True),
                                  Supplier.provides_parts.is_(True))
            .order_by(*_by_kind_then_name()).all())


def _next_code(kind):
    """The next free number in the kind's series: FP-004 after FP-003.

    Read off the codes in use rather than kept in a counter, so there is
    nothing to drift. Two people creating at the same instant could both read
    the same number -- the unique constraint catches that, and the save tries
    again with the next one.
    """
    prefix = CODE_PREFIX[kind] + "-"
    # No autoflush: on a create the new row is already in the session with no
    # code yet, and the query must not push it to the database before this
    # very function has given it one.
    with db.session.no_autoflush:
        taken = [c[0] for c in db.session.query(Supplier.code)
                 .filter(Supplier.code.like(prefix + "%")).all()]
    highest = 0
    for c in taken:
        try:
            highest = max(highest, int(c[len(prefix):]))
        except ValueError:
            pass
    return "%s%03d" % (prefix, highest + 1)


def _paid_sum():
    """What a bill has been paid, as SQL: its instalments added up.

    Every filter and every total on this page goes through here, so the screen
    and the database can never disagree about what is settled.
    """
    return (db.select(db.func.coalesce(db.func.sum(SupplierPayment.amount), 0))
            .where(SupplierPayment.invoice_id == SupplierInvoice.id)
            .scalar_subquery())


def _paid_expr():
    """The same sum, never counting past the invoice's own amount.

    An overpayment keyed in by mistake would otherwise eat into what other
    invoices still owe, and the total at the top would read short.
    """
    paid = _paid_sum()
    return db.case((paid > SupplierInvoice.amount, SupplierInvoice.amount),
                   else_=paid)


def _ids(name):
    """A repeated query param as ints — the supplier filter is tick boxes, so
    several can be asked for at once."""
    out = []
    for raw in request.args.getlist(name):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            pass
    return out


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _amount(raw, t):
    """A money field as it gets typed — grouped digits and all.
    Returns (int, None) or (None, error)."""
    value = parse_amount(raw)
    if value is None:
        return None, t["invoice.err.amount"]
    return value, None


def _get_invoice_or_404(iid):
    inv = db.session.get(SupplierInvoice, iid)
    if not inv:
        abort(404)
    return inv


def _period_bounds():
    """The window the list covers. Blank means everything, which is what you
    want on a page whose whole point is the old bill nobody has settled."""
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    return (date_from if _valid_date(date_from) else "",
            date_to if _valid_date(date_to) else "")


# ── The invoice form ─────────────────────────────────────────────────────────


def _read_invoice_form():
    """What the paper says: who sent it, when, for how much.
    Returns (data, None) or (None, error)."""
    t = get_t()

    supplier_id = request.form.get("supplier_id", type=int) or None
    if not supplier_id or not Supplier.query.filter_by(
            id=supplier_id, is_active=True).first():
        return None, t["invoice.err.supplier"]

    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["invoice.err.date"]

    # When the paper reached us. Not checked against the invoice's own date:
    # a supplier post-dating a bill is odd but real, and refusing it would only
    # send someone looking for a way round.
    received_on = (request.form.get("received_on") or "").strip()
    if received_on and not _valid_date(received_on):
        return None, t["invoice.err.received_on"]

    due_date = (request.form.get("due_date") or "").strip()
    if due_date and not _valid_date(due_date):
        return None, t["invoice.err.due_date"]
    if due_date and due_date < date_str:
        return None, t["invoice.err.due_before"]

    amount, error = _amount(request.form.get("amount"), t)
    if error:
        return None, error
    if amount <= 0:
        return None, t["invoice.err.amount"]

    # The order it settles, if any: one of this supplier's, and approved.
    po_id = request.form.get("purchase_order_id", type=int) or None
    if po_id:
        po = db.session.get(PurchaseOrder, po_id)
        if not po or po.supplier_id != supplier_id or po.status not in ("approved", "received_partial", "received"):
            return None, t["invoice.err.order"]

    return dict(
        supplier_id=supplier_id,
        purchase_order_id=po_id,
        number=(request.form.get("number") or "").strip() or None,
        date=date_str,
        received_on=received_on or None,
        due_date=due_date or None,
        amount=amount,
        currency="GNF",
        description=(request.form.get("description") or "").strip() or None,
    ), None


def _apply_photo_change(inv):
    """The optional document of the paper: a new scan (or, from older forms, a
    single photo) replaces and deletes the previous one; the remove button
    clears it. Returns a localized error when what was sent cannot be stored,
    else None. The row must already have an id.

    A scan is one or more flattened pages, bound into a single PDF on the way to
    storage. Photos saved before the scanner existed stay as they are.
    """
    t = get_t()
    old_key = inv.photo_key
    if request.form.get("photo_remove") == "1":
        inv.photo_key = None
    name = inv.supplier.name if inv.supplier else "facture"
    code = "%s-%s" % (name, inv.number or inv.id)
    month = (inv.date or "")[:7]
    # A received PDF, a scan, or (from forms older than the scanner) a single
    # photo -- one document per bill, so the first one present is the one kept.
    pdf_file = request.files.get("pdf_file")
    pages = [f for f in request.files.getlist("scan_pages") if f and f.filename]
    upload = request.files.get("photo")
    if pdf_file and pdf_file.filename:
        key, err = s3_storage.upload_invoice_pdf(pdf_file, code, month)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Document non enregistré.")
        inv.photo_key = key
    elif pages:
        key, err = s3_storage.upload_invoice_scan(pages, code, month)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Document non enregistré.")
        inv.photo_key = key
    elif upload and upload.filename:
        key, err = s3_storage.upload_invoice_photo(upload, code, month)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Photo non enregistrée.")
        inv.photo_key = key
    if old_key and old_key != inv.photo_key:
        s3_storage.delete_photo(old_key)
    return None


def _render_invoice_form(inv, error=None):
    tpl = "_invoice_form.html" if is_modal_request() else "invoice_form.html"
    status = 422 if (error and is_modal_request()) else 200
    # Every approved order, for the form to narrow to the supplier picked;
    # an order already on this bill stays offered whatever its state.
    orders = (PurchaseOrder.query
              .filter(db.or_(PurchaseOrder.status.in_(("approved", "received_partial", "received")),
                             PurchaseOrder.id == (inv.purchase_order_id if inv else 0)))
              .order_by(PurchaseOrder.date.desc(), PurchaseOrder.id.desc()).all())
    orders_json = [dict(id=o.id, supplier_id=o.supplier_id, supplier=o.supplier.name,
                        number=o.number, total=o.total, date=o.date)
                   for o in orders]
    # Opened from an order's page: that order and its supplier are set.
    preset = db.session.get(PurchaseOrder, request.args.get("po", type=int) or 0) if inv is None else None
    return render_template(tpl, invoice=inv, error=error,
                           suppliers=active_suppliers(), orders_json=orders_json,
                           preset_po=preset,
                           today=date.today().isoformat()), status


# ── Routes: the list ─────────────────────────────────────────────────────────


@supplier_invoices_bp.route("/factures")
@login_required
@require_perm("supplier_invoice.view")
def index():
    today = date.today().isoformat()
    date_from, date_to = _period_bounds()
    supplier_ids = _ids("supplier")
    status = request.args.get("status", "")
    if status not in STATUSES:
        status = ""

    q = SupplierInvoice.query
    if date_from:
        q = q.filter(SupplierInvoice.date >= date_from)
    if date_to:
        q = q.filter(SupplierInvoice.date <= date_to)
    if supplier_ids:
        q = q.filter(SupplierInvoice.supplier_id.in_(supplier_ids))
    search = (request.args.get("q") or "").strip()
    if search:
        # What someone remembers about a bill: its number, or a word of what it
        # was for.
        like = "%" + search + "%"
        # ...or the supplier's own code or name: "FP-003" finds its bills.
        q = q.join(Supplier).filter(db.or_(
            SupplierInvoice.number.ilike(like),
            SupplierInvoice.description.ilike(like),
            Supplier.code.ilike(like),
            Supplier.name.ilike(like)))
    if status == "paid":
        q = q.filter(_paid_sum() >= SupplierInvoice.amount)
    elif status in ("due", "overdue"):
        q = q.filter(_paid_sum() < SupplierInvoice.amount)
        if status == "overdue":
            q = q.filter(SupplierInvoice.due_date.isnot(None),
                         SupplierInvoice.due_date < today)

    # Totals off the query, not off the page: past 50 invoices the figures at
    # the top would otherwise cover only the slice on screen.
    sums = q.with_entities(
        db.func.coalesce(db.func.sum(SupplierInvoice.amount), 0),
        db.func.coalesce(db.func.sum(_paid_expr()), 0),
    ).one()
    billed, paid = int(sums[0] or 0), int(sums[1] or 0)

    pagination = q.order_by(SupplierInvoice.date.desc(),
                            SupplierInvoice.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE,
        error_out=False)

    # The suppliers tab can be narrowed to one kind.
    kind = request.args.get("kind", "")
    if kind not in SUPPLIER_KINDS:
        kind = ""
    sq = Supplier.query
    if kind:
        sq = sq.filter(Supplier.kind == kind)
    suppliers = sq.order_by(*_by_kind_then_name()).all()
    # What each supplier is still owed, in one grouped query rather than one
    # per row of the list.
    owed_rows = (db.session.query(
        SupplierInvoice.supplier_id,
        db.func.count(SupplierInvoice.id),
        db.func.coalesce(db.func.sum(SupplierInvoice.amount - _paid_expr()), 0))
        .group_by(SupplierInvoice.supplier_id).all())
    owed = {r[0]: {"count": int(r[1]), "remaining": int(r[2] or 0)} for r in owed_rows}

    tab = request.args.get("tab")
    if tab not in TABS:
        # Nothing recorded yet and nobody named either: open where the work
        # actually starts, which is saying who sends the bills.
        tab = "fournisseurs" if (not pagination.total and not suppliers) else "factures"
    kept = request.args.to_dict(flat=False)
    kept.pop("tab", None)
    # Switching tab starts at the top of the list, not on page 4 of the one you
    # were reading.
    kept.pop("page", None)
    tab_urls = {name: url_for("supplier_invoices.index", tab=name, **kept)
                for name in TABS}

    return render_template(
        "supplier_invoices.html",
        invoices=pagination.items, pagination=pagination,
        suppliers=suppliers, pickable=active_suppliers(), owed=owed,
        billed=billed, paid=paid, remaining=max(billed - paid, 0),
        tab=tab, tab_urls=tab_urls, statuses=STATUSES, status=status,
        supplier_kinds=SUPPLIER_KINDS, kind=kind,
        date_from=date_from, date_to=date_to, supplier_ids=supplier_ids,
        search=search, today=today,
    )


# ── Routes: an invoice ───────────────────────────────────────────────────────


@supplier_invoices_bp.route("/factures/nouvelle", methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_invoice_form()
        if error:
            return _render_invoice_form(None, error)
        inv = SupplierInvoice(created_by=current_user.id, **data)
        db.session.add(inv)
        db.session.flush()   # the photo's name is built from the row's own id
        perr = _apply_photo_change(inv)
        if perr:
            db.session.rollback()
            return _render_invoice_form(None, perr)
        log_action("CREATE", "supplier_invoice", resource_id=inv.id,
                   detail="Logged invoice of %s GNF from supplier #%s"
                          % (inv.amount, inv.supplier_id))
        db.session.commit()
        flash("success|" + t["invoice.created"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("supplier_invoices.index"))
    return _render_invoice_form(None)


@supplier_invoices_bp.route("/factures/<int:iid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.edit")
def edit(iid):
    inv = _get_invoice_or_404(iid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_invoice_form()
        if error:
            return _render_invoice_form(inv, error)
        for k, val in data.items():
            setattr(inv, k, val)
        perr = _apply_photo_change(inv)
        if perr:
            db.session.rollback()
            return _render_invoice_form(inv, perr)
        log_action("UPDATE", "supplier_invoice", resource_id=inv.id,
                   detail="Edited invoice #%s" % inv.id)
        db.session.commit()
        flash("success|" + t["invoice.updated"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("supplier_invoices.index"))
    return _render_invoice_form(inv)


@supplier_invoices_bp.route("/factures/<int:iid>/supprimer", methods=["POST"])
@login_required
@require_perm("supplier_invoice.delete")
def delete(iid):
    inv = _get_invoice_or_404(iid)
    photo_key = inv.photo_key
    db.session.delete(inv)
    log_action("DELETE", "supplier_invoice", resource_id=iid,
               detail="Deleted invoice #%s" % iid)
    db.session.commit()
    # Only once the row is gone for good, so a delete that fails never leaves
    # an invoice pointing at a photo that is no longer there.
    if photo_key:
        s3_storage.delete_photo(photo_key)
    flash("success|" + get_t()["invoice.deleted"])
    return redirect(request.referrer or url_for("supplier_invoices.index"))


# ── Routes: paying it ────────────────────────────────────────────────────────


@supplier_invoices_bp.route("/factures/<int:iid>/versements")
@login_required
@require_perm("supplier_invoice.view")
def payments(iid):
    """Everything paid against one bill, oldest first, and what is left."""
    inv = _get_invoice_or_404(iid)
    return render_template("invoice_payments.html", invoice=inv,
                           today=date.today().isoformat())


def _get_payment_or_404(iid, pid):
    pay = db.session.get(SupplierPayment, pid)
    if not pay or pay.invoice_id != iid:
        abort(404)
    return pay


def _render_payment_form(inv, pay, error=None):
    tpl = "_payment_form.html" if is_modal_request() else "payment_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, invoice=inv, payment=pay, error=error,
                           payment_methods=PAYMENT_METHODS,
                           accounts=active_accounts(),
                           today=date.today().isoformat()), status


def _read_payment_form(inv, pay):
    """One instalment: when, how much, how. Returns (data, None) or (None, err).

    The amount is held to what is still owed, counting every other instalment
    but this one — so correcting a payment downwards is never blocked by
    itself.
    """
    t = get_t()
    amount, error = _amount(request.form.get("amount"), t)
    if error:
        return None, error
    if amount <= 0:
        return None, t["invoice.err.amount"]

    others = sum(p.amount or 0 for p in inv.payments
                 if pay is None or p.id != pay.id)
    if amount > (inv.amount or 0) - others:
        return None, t["invoice.err.overpaid"]

    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["invoice.err.paid_date"]

    method = (request.form.get("method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, t["invoice.err.method"]

    # Where it came from, when known. Optional: a transfer whose account
    # nobody remembers is still a payment.
    account_id = request.form.get("account_id", type=int) or None
    if account_id and not CashAccount.query.filter_by(id=account_id, is_active=True).first():
        return None, t.get("caisse.err.unknown_account", "Choisissez un compte actif.")

    return dict(
        date=date_str, amount=amount, method=method, account_id=account_id,
        reference=(request.form.get("reference") or "").strip() or None,
    ), None


@supplier_invoices_bp.route("/factures/<int:iid>/versements/nouveau",
                            methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.edit")
def payment_new(iid):
    """An instalment paid by somebody other than the cash box — accounting
    wiring the balance, the boss settling it himself. What the box pays is
    entered on the Dépenses page instead, and arrives here on its own."""
    inv = _get_invoice_or_404(iid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_payment_form(inv, None)
        if error:
            return _render_payment_form(inv, None, error)
        pay = SupplierPayment(invoice_id=inv.id, created_by=current_user.id, **data)
        db.session.add(pay)
        log_action("CREATE", "supplier_payment", resource_id=inv.id,
                   detail="Paid %s GNF on invoice #%s" % (data["amount"], inv.id))
        db.session.commit()
        flash("success|" + t["invoice.payment_saved"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("supplier_invoices.payments", iid=inv.id))
    return _render_payment_form(inv, None)


@supplier_invoices_bp.route("/factures/<int:iid>/versements/<int:pid>/modifier",
                            methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.edit")
def payment_edit(iid, pid):
    inv = _get_invoice_or_404(iid)
    pay = _get_payment_or_404(iid, pid)
    t = get_t()
    if pay.from_cash_box:
        # It mirrors a cost in the cash box. Correcting it here would let the
        # bill and the box disagree about the same money.
        flash("error|" + t["invoice.err.cash_locked"])
        return redirect(url_for("supplier_invoices.payments", iid=inv.id))
    if request.method == "POST":
        data, error = _read_payment_form(inv, pay)
        if error:
            return _render_payment_form(inv, pay, error)
        for k, val in data.items():
            setattr(pay, k, val)
        log_action("UPDATE", "supplier_payment", resource_id=pay.id,
                   detail="Edited payment #%s on invoice #%s" % (pay.id, inv.id))
        db.session.commit()
        flash("success|" + t["invoice.payment_saved"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("supplier_invoices.payments", iid=inv.id))
    return _render_payment_form(inv, pay)


@supplier_invoices_bp.route("/factures/<int:iid>/versements/<int:pid>/supprimer",
                            methods=["POST"])
@login_required
@require_perm("supplier_invoice.edit")
def payment_delete(iid, pid):
    inv = _get_invoice_or_404(iid)
    pay = _get_payment_or_404(iid, pid)
    t = get_t()
    if pay.from_cash_box:
        flash("error|" + t["invoice.err.cash_locked"])
        return redirect(url_for("supplier_invoices.payments", iid=inv.id))
    db.session.delete(pay)
    log_action("DELETE", "supplier_payment", resource_id=pid,
               detail="Deleted payment #%s on invoice #%s" % (pid, inv.id))
    db.session.commit()
    flash("success|" + t["invoice.payment_deleted"])
    return redirect(url_for("supplier_invoices.payments", iid=inv.id))


# ── Routes: the suppliers ────────────────────────────────────────────────────


def _pickable_machines():
    """Machines a supplier can be given: the live ones in the user's fleets.
    Each comes with whoever holds it now, so the form can say so."""
    q = Vehicle.query.filter(Vehicle.deleted_at.is_(None))
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _assign_machines(row, chosen_ids):
    """Make `chosen_ids` this supplier's machines, among those the user may
    touch. A machine picked away from another supplier moves; one the user
    cannot see is left exactly as it is, whoever holds it."""
    for v in _pickable_machines():
        if v.id in chosen_ids:
            v.supplier_id = row.id
        elif v.supplier_id == row.id:
            v.supplier_id = None


def _save_supplier(row, parts_only=False):
    """Read the form onto the supplier. From the store (`parts_only`) the
    supplier sells parts by definition, whatever the form says."""
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = Supplier.query.filter(db.func.lower(Supplier.name) == name.lower())
    if row:
        clash = clash.filter(Supplier.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    kind = (request.form.get("kind") or "").strip()
    if kind not in SUPPLIER_KINDS:
        return t["supplier.err.kind"]
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(Supplier.sort_order)).scalar() or 0) + 1
        row = Supplier(sort_order=nxt, kind=kind, code=_next_code(kind))
        db.session.add(row)
    row.name = name
    row.contact = (request.form.get("contact") or "").strip() or None
    row.email = (request.form.get("email") or "").strip().lower()[:120] or None
    row.address = (request.form.get("address") or "").strip()[:200] or None
    row.note = (request.form.get("note") or "").strip() or None
    row.provides_machines = request.form.get("provides_machines") is not None
    row.provides_parts = parts_only or request.form.get("provides_parts") is not None
    # A new supplier is coded in its kind's series; one that changes kind moves
    # to the other series and its old code is let go -- only the current one
    # is ever shown.
    needs_code = creating or row.kind != kind
    if not creating and needs_code:
        row.code = _next_code(kind)
    row.kind = kind
    db.session.flush()
    # A lessor's machines follow the boxes ticked; one that stops being a
    # lessor lets its machines go.
    chosen = set()
    if row.provides_machines:
        for raw in request.form.getlist("machine_ids"):
            try:
                chosen.add(int(raw))
            except (TypeError, ValueError):
                pass
    _assign_machines(row, chosen)
    log_action("CREATE" if creating else "UPDATE", "supplier", resource_id=row.id,
               detail="%s supplier '%s' (%s)" % ("Created" if creating else "Updated",
                                                 row.name, row.code))
    # Two saves at the same instant can draw the same code; the constraint
    # refuses the second, which simply takes the next number.
    for attempt in range(3):
        try:
            db.session.commit()
            return None
        except IntegrityError:
            db.session.rollback()
            if not needs_code or attempt == 2:
                return t.get("list.err.name_taken", "Ce nom existe déjà.")
            row.code = _next_code(kind)
            row = db.session.merge(row)
    return None


def _render_supplier_form(row, error=None):
    tpl = "_supplier_form.html" if is_modal_request() else "supplier_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error,
                           machines=_pickable_machines()), status


def _suppliers_url():
    """Back to the suppliers list the user may open: the bills page's tab, or
    the standalone page the store reaches."""
    if has_perm("supplier_invoice.view"):
        return url_for("supplier_invoices.index", tab="fournisseurs")
    return url_for("supplier_invoices.suppliers")


@supplier_invoices_bp.route("/fournisseurs")
@login_required
@require_any_perm("supplier_invoice.view", "stock.view")
def suppliers():
    """The one list of suppliers, on its own page: the store buys from the
    same people accounting pays, so it reaches the same list. What each is
    owed is only shown to whoever may see the bills."""
    kind = request.args.get("kind", "")
    if kind not in SUPPLIER_KINDS:
        kind = ""
    sq = Supplier.query
    if kind:
        sq = sq.filter(Supplier.kind == kind)
    rows = sq.order_by(*_by_kind_then_name()).all()
    owed = {}
    if has_perm("supplier_invoice.view"):
        owed_rows = (db.session.query(
            SupplierInvoice.supplier_id,
            db.func.count(SupplierInvoice.id),
            db.func.coalesce(db.func.sum(SupplierInvoice.amount - _paid_expr()), 0))
            .group_by(SupplierInvoice.supplier_id).all())
        owed = {r[0]: {"count": int(r[1]), "remaining": int(r[2] or 0)} for r in owed_rows}
    return render_template("suppliers.html", suppliers=rows, owed=owed,
                           supplier_kinds=SUPPLIER_KINDS, kind=kind,
                           can_manage=has_perm("supplier_invoice.create") or has_perm("stock.manage"))


@supplier_invoices_bp.route("/fournisseurs/nouveau", methods=["GET", "POST"])
@login_required
@require_any_perm("supplier_invoice.create", "stock.manage")
def supplier_new():
    if request.method == "POST":
        error = _save_supplier(None)
        if error:
            return _render_supplier_form(None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(_suppliers_url())
    return _render_supplier_form(None)


@supplier_invoices_bp.route("/fournisseurs/<int:sid>/modifier", methods=["GET", "POST"])
@login_required
@require_any_perm("supplier_invoice.create", "stock.manage")
def supplier_edit(sid):
    row = db.session.get(Supplier, sid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_supplier(row)
        if error:
            return _render_supplier_form(row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(_suppliers_url())
    return _render_supplier_form(row)


@supplier_invoices_bp.route(
    "/fournisseurs/<int:sid>/<any(archive,reactivate,delete):what>", methods=["POST"])
@login_required
@require_any_perm("supplier_invoice.create", "stock.manage")
def supplier_action(sid, what):
    """Archive takes it out of the pickers and leaves its invoices named;
    delete is only for one that has never been billed against."""
    row = db.session.get(Supplier, sid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if SupplierInvoice.query.filter_by(supplier_id=row.id).count():
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(_suppliers_url())
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "supplier", resource_id=sid,
                   detail="Deleted supplier '%s'" % name)
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "supplier", resource_id=sid,
                   detail="%s supplier '%s'" % (what.title(), row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(_suppliers_url())
