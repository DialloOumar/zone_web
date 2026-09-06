"""Factures fournisseurs — the bills the company has received and owes.

The opposite direction from the facturation screen, which bills clients: here
a garage, a parts shop or a landlord sends us a paper, it is recorded with its
amount and its due date, and the page says what is still owed and to whom.

It is a register, not a till. Nothing written here touches the cash box or the
expense ledger: the money actually leaving is logged as an expense like any
other, and an invoice counted in both places would be the same money twice.

Nothing about a payment is stored that can be worked out. Paid, part-paid and
untouched all follow from `paid_amount`, so a status can never drift away from
the figures it is supposed to describe.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from app import get_t, is_modal_request, log_action, modal_ok, require_perm
# The one list of ways money changes hands, shared with the cash box and every
# other screen that records a payment, so a method added there shows up here.
from blueprints.expenses import PAYMENT_METHODS
from models import Supplier, SupplierInvoice, db

supplier_invoices_bp = Blueprint("supplier_invoices", __name__)

# The two halves of the page, shown one at a time: the bills, and the people
# who send them.
TABS = ("factures", "fournisseurs")

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
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def active_suppliers():
    """Who a new invoice can be filed under. An archived supplier keeps its
    invoices and their history, it is simply no longer offered."""
    return (Supplier.query.filter(Supplier.is_active.is_(True))
            .order_by(Supplier.sort_order, Supplier.name).all())


def _paid_expr():
    """What has been paid, never counting past the invoice's own amount.

    An overpayment keyed in by mistake would otherwise eat into what other
    invoices still owe, and the total at the top would read short.
    """
    return db.case(
        (SupplierInvoice.paid_amount > SupplierInvoice.amount, SupplierInvoice.amount),
        else_=SupplierInvoice.paid_amount,
    )


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
    """A money field as it gets typed — spaces, commas and all.
    Returns (int, None) or (None, error)."""
    raw = (raw or "").strip().replace(" ", "").replace(",", "")
    try:
        return int(round(float(raw))), None
    except (TypeError, ValueError):
        return None, t["invoice.err.amount"]


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

    return dict(
        supplier_id=supplier_id,
        number=(request.form.get("number") or "").strip() or None,
        date=date_str,
        due_date=due_date or None,
        amount=amount,
        currency="GNF",
        description=(request.form.get("description") or "").strip() or None,
    ), None


def _apply_photo_change(inv):
    """The optional photo of the paper: a new upload replaces (and deletes) the
    previous one, the remove button clears it. Returns a localized error when a
    submitted photo cannot be stored, else None. The row must already have an id.
    """
    t = get_t()
    old_key = inv.photo_key
    if request.form.get("photo_remove") == "1":
        inv.photo_key = None
    upload = request.files.get("photo")
    if upload and upload.filename:
        name = inv.supplier.name if inv.supplier else "facture"
        key, err = s3_storage.upload_invoice_photo(
            upload, "%s-%s" % (name, inv.number or inv.id), (inv.date or "")[:7])
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
    return render_template(tpl, invoice=inv, error=error,
                           suppliers=active_suppliers(),
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
        q = q.filter(db.or_(SupplierInvoice.number.ilike(like),
                            SupplierInvoice.description.ilike(like)))
    if status == "paid":
        q = q.filter(SupplierInvoice.paid_amount >= SupplierInvoice.amount)
    elif status in ("due", "overdue"):
        q = q.filter(SupplierInvoice.paid_amount < SupplierInvoice.amount)
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

    suppliers = Supplier.query.order_by(Supplier.sort_order, Supplier.name).all()
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
        inv = SupplierInvoice(created_by=current_user.id, paid_amount=0, **data)
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


def _render_payment_form(inv, error=None):
    tpl = "_payment_form.html" if is_modal_request() else "payment_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, invoice=inv, error=error,
                           payment_methods=PAYMENT_METHODS,
                           today=date.today().isoformat()), status


@supplier_invoices_bp.route("/factures/<int:iid>/paiement", methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.edit")
def payment(iid):
    """What has been settled on this bill. One figure, corrected as it is paid:
    part of it today, the rest next month, and zero to undo a mistake."""
    inv = _get_invoice_or_404(iid)
    t = get_t()
    if request.method == "POST":
        amount, error = _amount(request.form.get("paid_amount"), t)
        if error:
            return _render_payment_form(inv, error)
        if amount < 0:
            return _render_payment_form(inv, t["invoice.err.amount"])
        if amount > inv.amount:
            return _render_payment_form(inv, t["invoice.err.overpaid"])

        paid_date = (request.form.get("paid_date") or "").strip()
        method = (request.form.get("payment_method") or "").strip()
        if amount > 0:
            if not _valid_date(paid_date):
                return _render_payment_form(inv, t["invoice.err.paid_date"])
            if method not in PAYMENT_METHODS:
                return _render_payment_form(inv, t["invoice.err.method"])

        inv.paid_amount = amount
        if amount > 0:
            inv.paid_date = paid_date
            inv.payment_method = method
            inv.payment_reference = (request.form.get("payment_reference") or "").strip() or None
        else:
            # Nothing paid leaves nothing to remember about a payment.
            inv.paid_date = inv.payment_method = inv.payment_reference = None
        log_action("UPDATE", "supplier_invoice", resource_id=inv.id,
                   detail="Payment on invoice #%s now %s GNF" % (inv.id, amount))
        db.session.commit()
        flash("success|" + t["invoice.payment_saved"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("supplier_invoices.index"))
    return _render_payment_form(inv)


# ── Routes: the suppliers ────────────────────────────────────────────────────


def _save_supplier(row):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = Supplier.query.filter(db.func.lower(Supplier.name) == name.lower())
    if row:
        clash = clash.filter(Supplier.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(Supplier.sort_order)).scalar() or 0) + 1
        row = Supplier(sort_order=nxt)
        db.session.add(row)
    row.name = name
    row.contact = (request.form.get("contact") or "").strip() or None
    row.note = (request.form.get("note") or "").strip() or None
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "supplier", resource_id=row.id,
               detail="%s supplier '%s'" % ("Created" if creating else "Updated", row.name))
    db.session.commit()
    return None


def _render_supplier_form(row, error=None):
    tpl = "_supplier_form.html" if is_modal_request() else "supplier_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


def _suppliers_url():
    return url_for("supplier_invoices.index", tab="fournisseurs")


@supplier_invoices_bp.route("/fournisseurs/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("supplier_invoice.create")
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
@require_perm("supplier_invoice.create")
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
@require_perm("supplier_invoice.create")
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
