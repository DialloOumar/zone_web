"""Bons de commande — what the store orders from a supplier, and who signed.

The company's paper order carries two signatures, responsable logistique and
responsable financière. Here those are two boxes on a role (Administration >
Rôles): a holder of a role with the box gives that approval. An order made by
someone holding the logistics box is born with the first approval already
given, and waits for the second.

States: à approuver (logistique), à approuver (finance), approuvé, refusé.
Refusing takes a reason; a refused order is corrected and resubmitted, and
every refusal stays on it. Editing an order that already had an approval
takes the approval back -- what was signed is not what is now written.

Prices are known at order time; a discount is no longer typed (the column stays at zero), summed
at the foot. Numbers are BC-2026-001, per year of issue, given by the app.
Receiving: an approved order is received line by line, in the store's own
unit (a drum ordered per pack comes in as litres), and each quantity writes
a receipt into the store at the line's unit cost -- no expense, since the
money side of an order is the supplier's bill. Tying that bill to the order
is the next step.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app import (_get_setting, current_lang, get_t, has_perm, log_action,
                 parse_amount, require_perm)
from blueprints.stock import active_units, buying_units_for, unit_codes
from blueprints.supplier_invoices import _save_supplier, parts_suppliers
from models import Part, PurchaseOrder, PurchaseOrderLine, StockMovement, Supplier, db

purchases_bp = Blueprint("purchases", __name__, url_prefix="/stock/commandes")

STATUSES = ("pending_logistics", "pending_finance", "approved", "rejected",
            "received_partial", "received")
PENDING = ("pending_logistics", "pending_finance")
RECEIVABLE = ("approved", "received_partial")
PER_PAGE = 50


# ── Who signs ────────────────────────────────────────────────────────────────

def user_approves(kind):
    """Whether the current user holds the logistics or finance box on any of
    their roles. Super admins sign anything."""
    if not current_user.is_authenticated:
        return False
    if current_user.is_super_admin:
        return True
    flag = "approves_" + kind
    return any(uf.role and getattr(uf.role, flag, False) for uf in current_user.user_fleets)


def _valid_date(s):
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def _next_number(issue_date):
    """BC-2026-004 after BC-2026-003, per year. Read off the numbers in use;
    the unique constraint catches a tie."""
    prefix = "BC-%s-" % issue_date[:4]
    with db.session.no_autoflush:
        taken = [n[0] for n in db.session.query(PurchaseOrder.number)
                 .filter(PurchaseOrder.number.like(prefix + "%")).all()]
    highest = 0
    for n in taken:
        try:
            highest = max(highest, int(n[len(prefix):]))
        except ValueError:
            pass
    return "%s%03d" % (prefix, highest + 1)


def _get_or_404(oid):
    po = db.session.get(PurchaseOrder, oid)
    if not po:
        abort(404)
    return po


def _reset_approvals(po):
    """Where a freshly written order stands: the logistics signature is given
    by itself when the writer holds that box, the finance one is always a
    click of its own."""
    po.logistics_by = po.logistics_at = None
    po.finance_by = po.finance_at = None
    if user_approves("logistics"):
        po.logistics_by, po.logistics_at = current_user.id, datetime.utcnow()
        po.status = "pending_finance"
    else:
        po.status = "pending_logistics"


# ── The form ─────────────────────────────────────────────────────────────────

def _read_form(po):
    """Read the header and the lines. Returns (header, lines, None) or
    (None, None, error). Lines with nothing typed on them are skipped."""
    t = get_t()
    supplier = db.session.get(Supplier, request.form.get("supplier_id", type=int) or 0)
    if not supplier or not supplier.is_active or not supplier.provides_parts:
        return None, None, t["po.err.supplier"]
    date_str = (request.form.get("date") or "").strip() or date.today().isoformat()
    if not _valid_date(date_str):
        return None, None, t.get("client.err.date", "Date invalide.")

    part_ids = request.form.getlist("part_id")
    descs = request.form.getlist("description")
    refs = request.form.getlist("reference")
    qtys = request.form.getlist("quantity")
    prices = request.form.getlist("unit_price")
    units = request.form.getlist("unit")
    modes = request.form.getlist("mode")
    new_units = request.form.getlist("new_unit")
    lines = []
    for i in range(max(len(descs), len(qtys), len(prices))):
        pid = (part_ids[i] if i < len(part_ids) else "").strip()
        desc = (descs[i] if i < len(descs) else "").strip()
        ref = (refs[i] if i < len(refs) else "").strip()
        qty_raw = (qtys[i] if i < len(qtys) else "").strip().replace(",", ".")
        price_raw = (prices[i] if i < len(prices) else "").strip()
        unit_choice = (units[i] if i < len(units) else "").strip()
        mode = (modes[i] if i < len(modes) else "new").strip() or "new"
        new_unit = (new_units[i] if i < len(new_units) else "piece").strip() or "piece"
        if not (pid or desc or qty_raw or price_raw):
            continue
        part = None
        if mode == "stock":
            # "Stock" ticked: the article must be one the store knows, by id
            # from the list or by its exact name typed in.
            part = db.session.get(Part, int(pid)) if pid.isdigit() else None
            if not part and desc:
                part = Part.query.filter(db.func.lower(Part.name) == desc.lower()).first()
            if not part:
                return None, None, t["po.err.pick_part"] % (desc or "?")
        else:
            if not desc:
                return None, None, t["po.err.description"]
            if new_unit not in unit_codes():
                return None, None, t.get("part.err.unit", "Choisissez une unité.")
            # A name the catalogue already has is that article, not a second one.
            part = Part.query.filter(db.func.lower(Part.name) == desc.lower()).first()
        try:
            qty = float(qty_raw)
        except ValueError:
            qty = 0
        if qty <= 0:
            return None, None, t["po.err.quantity"]
        price = parse_amount(price_raw)
        if price is None or price < 0:
            return None, None, t["po.err.price"]
        discount = 0     # no discount is typed any more; the column stays, at zero
        # What the line is bought in: the part's own unit, or a buying unit
        # that converts to it ("fût" for a part counting in litres). The
        # conversion is copied onto the line as it stands today.
        buy_unit, factor = None, 1.0
        if part and unit_choice and unit_choice != "stock":
            bu = next(((u, f) for u, f in buying_units_for(part.unit) if u.code == unit_choice), None)
            if not bu:
                return None, None, t["po.err.buy_unit"]
            buy_unit, factor = bu[0].code, float(bu[1])
        lines.append(dict(part_id=part.id if part else None,
                          description=(desc or part.name)[:160], reference=ref[:60] or None,
                          quantity=qty, buy_unit=buy_unit, factor=factor, unit_price=price, discount=discount,
                          new_unit=None if part else new_unit,
                          amount=int(round(qty * price)) - discount))
    if not lines:
        return None, None, t["po.err.no_lines"]
    header = dict(supplier_id=supplier.id, date=date_str,
                  note=(request.form.get("note") or "").strip()[:255] or None)
    return header, lines, None


def _render_form(po, error=None, lines=None):
    """`lines` is what to show: the order's own, or what was just typed."""
    if lines is None:
        lines = [dict(part_id=l.part_id, description=l.description, reference=l.reference or "",
                      quantity=l.quantity, unit_price=l.unit_price, discount=l.discount,
                      unit=l.buy_unit or "stock", new_unit=l.new_unit,
                      mode="stock" if l.part_id else "new")
                 for l in (po.lines if po else [])]
    if request.method == "POST":
        lines = []
        for i in range(len(request.form.getlist("description"))):
            g = lambda k: (request.form.getlist(k)[i] if i < len(request.form.getlist(k)) else "")
            lines.append(dict(part_id=g("part_id"), description=g("description"), reference=g("reference"),
                              quantity=g("quantity"), unit_price=g("unit_price"), discount=g("discount"),
                              unit=g("unit"), new_unit=g("new_unit"), mode=g("mode") or "new"))
    t = get_t()
    parts = Part.query.filter(Part.is_active.is_(True)).order_by(Part.name).all()
    from app import _unit_label
    # For each part: its own unit and the buying units that convert to it,
    # so the line's unit box offers "litre" or "fût (200 litre)".
    parts_json = [dict(id=p.id, name=p.name, unit=_unit_label(p.unit),
                       buy=[dict(code=u.code, name=u.name, factor=f)
                            for u, f in buying_units_for(p.unit)])
                  for p in parts]
    return render_template("purchase_order_form.html", po=po, error=error, lines=lines,
                           suppliers=parts_suppliers(), parts_json=parts_json,
                           units=active_units(),
                           today=date.today().isoformat())


def _write_lines(po, lines):
    po.lines.clear()
    for l in lines:
        po.lines.append(PurchaseOrderLine(**l))
    po.total_gross = sum(int(round(l["quantity"] * l["unit_price"])) for l in lines)
    po.total_discount = sum(l["discount"] for l in lines)
    po.total = po.total_gross - po.total_discount


# ── The register ─────────────────────────────────────────────────────────────

@purchases_bp.route("/")
@login_required
@require_perm("stock.view")
def index():
    status = request.args.get("status") or ""
    if status not in STATUSES + ("pending",):
        status = ""
    q = PurchaseOrder.query
    if status == "pending":
        q = q.filter(PurchaseOrder.status.in_(PENDING))
    elif status:
        q = q.filter(PurchaseOrder.status == status)
    search = (request.args.get("q") or "").strip()
    if search:
        like = "%" + search + "%"
        q = q.join(Supplier).filter(db.or_(PurchaseOrder.number.ilike(like), Supplier.name.ilike(like),
                                           Supplier.code.ilike(like), PurchaseOrder.note.ilike(like)))
    pagination = (q.order_by(PurchaseOrder.date.desc(), PurchaseOrder.id.desc())
                  .paginate(page=request.args.get("page", 1, type=int), per_page=PER_PAGE, error_out=False))
    counts = dict(db.session.query(PurchaseOrder.status, db.func.count(PurchaseOrder.id))
                  .group_by(PurchaseOrder.status).all())
    # What waits for the person looking: the signatures they can give.
    mine = 0
    if user_approves("logistics"):
        mine += counts.get("pending_logistics", 0)
    if user_approves("finance"):
        mine += counts.get("pending_finance", 0)
    return render_template("purchase_orders.html", orders=pagination.items, pagination=pagination,
                           status=status, search=search, counts=counts, mine=mine,
                           statuses=STATUSES, can_create=has_perm("stock.manage"),
                           parts_count=Part.query.filter(Part.is_active.is_(True)).count(),
                           orders_count=PurchaseOrder.query.count())


# ── Create, edit ─────────────────────────────────────────────────────────────

@purchases_bp.route("/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def new():
    t = get_t()
    if request.method == "POST":
        header, lines, error = _read_form(None)
        if error:
            return _render_form(None, error)
        po = PurchaseOrder(number=_next_number(header["date"]), requested_by=current_user.id,
                           requester_name=current_user.full_name,
                           requester_phone=current_user.phone or current_user.email, **header)
        _write_lines(po, lines)
        _reset_approvals(po)
        db.session.add(po)
        for attempt in range(3):
            try:
                db.session.commit()
                break
            except IntegrityError:
                db.session.rollback()
                if attempt == 2:
                    abort(500)
                po.number = _next_number(header["date"])
                po = db.session.merge(po)
        log_action("CREATE", "purchase_order", resource_id=po.id,
                   detail="Order %s to %s: %d GNF" % (po.number, po.supplier.name, po.total))
        db.session.commit()
        flash("success|" + t["po.created"])
        return redirect(url_for("purchases.detail", oid=po.id))
    return _render_form(None)


@purchases_bp.route("/fournisseur/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def supplier_new():
    """A parts supplier written down on the way to an order, without leaving
    the store: the same supplier as the bills know, minus the machines a
    lessor would tick. Saved, the new order opens with it chosen."""
    t = get_t()
    if request.method == "POST":
        error = _save_supplier(None, parts_only=True)
        if error:
            return render_template("purchase_supplier_form.html", error=error), 422
        name = (request.form.get("name") or "").strip()
        row = Supplier.query.filter(db.func.lower(Supplier.name) == name.lower()).first()
        flash("success|" + t.get("list.created", "Ajouté."))
        return redirect(url_for("purchases.new", supplier=row.id if row else None))
    return render_template("purchase_supplier_form.html", error=None)


def _editable(po):
    """While nobody has fully signed it. An approved order is what the
    supplier was sent: it does not move."""
    return po.status in PENDING or po.status == "rejected"


@purchases_bp.route("/<int:oid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def edit(oid):
    po = _get_or_404(oid)
    t = get_t()
    if not _editable(po):
        flash("error|" + t["po.err.locked"])
        return redirect(url_for("purchases.detail", oid=po.id))
    if request.method == "POST":
        header, lines, error = _read_form(po)
        if error:
            return _render_form(po, error)
        for k, v in header.items():
            setattr(po, k, v)
        _write_lines(po, lines)
        # What was signed is not what is now written: the signatures are
        # given again, and a refusal is answered by this new version.
        _reset_approvals(po)
        po.rejected_by = po.rejected_at = po.rejected_reason = None
        log_action("UPDATE", "purchase_order", resource_id=po.id,
                   detail="Order %s rewritten: %d GNF" % (po.number, po.total))
        db.session.commit()
        flash("success|" + t["po.updated"])
        return redirect(url_for("purchases.detail", oid=po.id))
    return _render_form(po)


# ── One order ────────────────────────────────────────────────────────────────

@purchases_bp.route("/<int:oid>")
@login_required
@require_perm("stock.view")
def detail(oid):
    po = _get_or_404(oid)
    return render_template("purchase_order.html", po=po,
                           can_sign_logistics=po.status == "pending_logistics" and user_approves("logistics"),
                           can_sign_finance=po.status == "pending_finance" and user_approves("finance"),
                           can_reject=po.status in PENDING and (user_approves("logistics") or user_approves("finance")),
                           can_edit=has_perm("stock.manage") and _editable(po),
                           can_receive=has_perm("stock.manage") and po.status in RECEIVABLE,
                           can_delete=(current_user.is_super_admin or has_perm("stock.manage")) and _editable(po))


@purchases_bp.route("/<int:oid>/approuver/<any(logistics,finance):kind>", methods=["POST"])
@login_required
def approve(oid, kind):
    po = _get_or_404(oid)
    t = get_t()
    if not user_approves(kind):
        abort(403)
    expected = "pending_logistics" if kind == "logistics" else "pending_finance"
    if po.status != expected:
        flash("error|" + t["po.err.not_pending"])
        return redirect(url_for("purchases.detail", oid=po.id))
    now = datetime.utcnow()
    if kind == "logistics":
        po.logistics_by, po.logistics_at, po.status = current_user.id, now, "pending_finance"
    else:
        po.finance_by, po.finance_at, po.status = current_user.id, now, "approved"
    log_action("APPROVE", "purchase_order", resource_id=po.id,
               detail="%s signed %s (%s)" % (current_user.full_name, po.number, kind))
    db.session.commit()
    flash("success|" + (t["po.approved_final"] if po.status == "approved" else t["po.approved_step"]))
    return redirect(url_for("purchases.detail", oid=po.id))


@purchases_bp.route("/<int:oid>/refuser", methods=["POST"])
@login_required
def reject(oid):
    po = _get_or_404(oid)
    t = get_t()
    if not (user_approves("logistics") or user_approves("finance")):
        abort(403)
    if po.status not in PENDING:
        flash("error|" + t["po.err.not_pending"])
        return redirect(url_for("purchases.detail", oid=po.id))
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("error|" + t["po.err.reason"])
        return redirect(url_for("purchases.detail", oid=po.id))
    po.status = "rejected"
    po.rejected_by, po.rejected_at, po.rejected_reason = current_user.id, datetime.utcnow(), reason[:255]
    po.rejections = (po.rejections or 0) + 1
    log_action("REJECT", "purchase_order", resource_id=po.id,
               detail="%s refused %s: %s" % (current_user.full_name, po.number, reason[:120]))
    db.session.commit()
    flash("success|" + t["po.rejected"])
    return redirect(url_for("purchases.detail", oid=po.id))


@purchases_bp.route("/<int:oid>/supprimer", methods=["POST"])
@login_required
@require_perm("stock.manage")
def delete(oid):
    po = _get_or_404(oid)
    t = get_t()
    if not _editable(po):
        flash("error|" + t["po.err.locked"])
        return redirect(url_for("purchases.detail", oid=po.id))
    number = po.number
    db.session.delete(po)
    log_action("DELETE", "purchase_order", resource_id=oid, detail="Deleted order %s" % number)
    db.session.commit()
    flash("success|" + t["po.deleted"])
    return redirect(url_for("purchases.index"))


@purchases_bp.route("/<int:oid>/imprimer")
@login_required
@require_perm("stock.view")
def print_sheet(oid):
    """The order as a sheet, after the company's own: header, submitted by,
    the supplier, the lines with their discount, and the two signature
    blocks naming who signed and when."""
    po = _get_or_404(oid)
    from blueprints.invoicing import invoice_settings
    return render_template("purchase_order_print.html", po=po, co=invoice_settings(),
                           currency=_get_setting("currency", "GNF"))


# ── Receiving ────────────────────────────────────────────────────────────────

@purchases_bp.route("/<int:oid>/reception", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def receive(oid):
    """What arrived, line by line, in the store's own unit: a drum ordered
    as "1 fût" comes in as 200 litres, at the litre price the line works out
    to. Each quantity typed writes a receipt into the store on its own. No
    expense is written: the money side of an order is the supplier's bill.
    A line with no catalogue part enters no stock; it is only ticked off."""
    po = _get_or_404(oid)
    t = get_t()
    if po.status not in RECEIVABLE:
        flash("error|" + t["po.err.not_receivable"])
        return redirect(url_for("purchases.detail", oid=po.id))
    if request.method == "POST":
        date_str = (request.form.get("date") or "").strip() or date.today().isoformat()
        if not _valid_date(date_str):
            return render_template("purchase_order_receive.html", po=po, error=t.get("client.err.date", "Date invalide."),
                                   today=date.today().isoformat())
        written = 0
        for line in po.lines:
            raw = (request.form.get("recv_%d" % line.id) or "").strip().replace(",", ".")
            if not raw:
                continue
            try:
                qty = float(raw)
            except ValueError:
                qty = -1
            if qty < 0 or qty > line.remaining_qty + 1e-9:
                return render_template("purchase_order_receive.html", po=po, error=t["po.err.receive_qty"],
                                       today=date.today().isoformat())
            if qty == 0:
                continue
            if not line.part_id and not request.form.get("skip_%d" % line.id):
                # A new-article line becomes a catalogue part now, in the unit
                # the order gave it, so what arrives is counted; a part of that
                # name already there is simply used.
                unit = line.new_unit if line.new_unit in unit_codes() else "piece"
                part = Part.query.filter(db.func.lower(Part.name) == line.description.lower()).first()
                if not part:
                    part = Part(name=line.description[:120], unit=unit, created_by=current_user.id)
                    db.session.add(part)
                    db.session.flush()
                line.part_id = part.id
            if line.part_id:
                db.session.add(StockMovement(
                    part_id=line.part_id, kind="entree", date=date_str, quantity=qty,
                    unit_price=line.stock_unit_price, supplier=po.supplier.name,
                    note=po.number, purchase_line_id=line.id, created_by=current_user.id))
            line.received_qty = (line.received_qty or 0) + qty
            written += 1
        if not written:
            return render_template("purchase_order_receive.html", po=po, error=t["po.err.receive_none"],
                                   today=date.today().isoformat())
        po.status = "received" if all(l.remaining_qty <= 1e-9 for l in po.lines) else "received_partial"
        log_action("RECEIVE", "purchase_order", resource_id=po.id,
                   detail="%s: %d line(s) received, now %s" % (po.number, written, po.status))
        db.session.commit()
        flash("success|" + (t["po.received_all"] if po.status == "received" else t["po.received_part"]))
        return redirect(url_for("purchases.detail", oid=po.id))
    return render_template("purchase_order_receive.html", po=po, error=None, today=date.today().isoformat())
