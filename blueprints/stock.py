"""Stock blueprint — the workshop store of maintenance parts.

One store for the whole company: unlike vehicles or citernes, a part is not
owned by a fleet, so nothing here is fleet-scoped. What ties a part to a fleet
is the vehicle it ends up on, and that link is carried by the sortie.

A part holds no quantity of its own — what is on hand is computed from its
movements (see Part.quantity_as_of), the same arrangement as a citerne. This
first slice is the catalogue: create, edit, archive, and the opening stock.
Receipts, issues and counts reuse the same movement table in the next step.

Helpers (require_perm, log_action, get_t, modal helpers) come from app.py; this
module is imported at the bottom of app.py.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from app import get_t, is_modal_request, log_action, modal_ok, require_perm
from blueprints.expenses import PARTS_CATEGORY, PAYMENT_METHODS
from models import Expense, Part, StockMovement, db

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

# How a part is counted. Anything sold by the piece uses "piece"; oil and
# grease go by the litre or the kilo; a set is 4 tyres bought as one.
UNITS = ["piece", "litre", "kg", "set"]

# Kinds the store screen writes directly. "sortie" and "retour" are written by
# a service record, so they are read-only here — they show in the history but
# have no button of their own.
MOVEMENT_KINDS = ("initial", "entree", "sortie", "retour", "inventaire")

HISTORY_LIMIT = 300


def _get_part_or_404(pid):
    p = db.session.get(Part, pid)
    if not p:
        abort(404)
    return p


def _get_movement_or_404(mid):
    m = db.session.get(StockMovement, mid)
    if not m:
        abort(404)
    return m


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _active_parts():
    return Part.query.filter(Part.is_active.is_(True)).order_by(Part.name).all()


# ── Catalogue ────────────────────────────────────────────────────────────────


@stock_bp.route("/")
@login_required
@require_perm("stock.view")
def index():
    show_archived = request.args.get("archived") == "1"
    search = (request.args.get("q") or "").strip()

    q = Part.query.filter(Part.is_active.is_(not show_archived))
    if search:
        q = q.filter(Part.name.ilike(f"%{search}%"))
    parts = q.order_by(Part.name).all()

    archived_count = Part.query.filter(Part.is_active.is_(False)).count()
    # Totals over what is shown, so a search narrows them too. A part with no
    # price known contributes nothing rather than breaking the sum.
    total_value = sum(p.stock_value or 0 for p in parts)
    low_count = sum(1 for p in parts if p.below_reorder)

    recent = (StockMovement.query
              .order_by(StockMovement.date.desc(), StockMovement.id.desc())
              .limit(15).all())

    return render_template(
        "stock.html", parts=parts, show_archived=show_archived, search=search,
        archived_count=archived_count, total_value=total_value,
        low_count=low_count, recent=recent, active_parts=_active_parts(),
        today=date.today().isoformat())


# ── Create / edit ────────────────────────────────────────────────────────────


def _read_part_form(part):
    """Validate the part form. Returns (data, error); `opening` and its price
    ride along in the data and are split out by the caller — they are movement
    fields, not columns of Part."""
    t = get_t()
    name = (request.form.get("name") or "").strip()
    unit = (request.form.get("unit") or "").strip()

    if not name:
        return None, t.get("part.err.name", "La désignation est obligatoire.")
    if unit not in UNITS:
        return None, t.get("part.err.unit", "Choisissez une unité.")

    clash = Part.query.filter(db.func.lower(Part.name) == name.lower())
    if part:
        clash = clash.filter(Part.id != part.id)
    if clash.first():
        return None, t.get("part.err.name_taken", "Cette désignation existe déjà.")

    def _num(raw, cast):
        raw = (raw or "").replace(" ", "").strip()
        if not raw:
            return None, False
        try:
            return cast(raw), False
        except ValueError:
            return None, True

    unit_price, bad = _num(request.form.get("unit_price"), lambda s: int(round(float(s))))
    if bad or (unit_price is not None and unit_price < 0):
        return None, t.get("part.err.price", "Prix invalide.")

    reorder, bad = _num(request.form.get("reorder_level"), float)
    if bad or (reorder is not None and reorder < 0):
        return None, t.get("part.err.reorder", "Seuil invalide.")

    opening, bad = _num(request.form.get("opening_quantity"), float)
    if bad or (opening is not None and opening < 0):
        return None, t.get("part.err.opening", "Quantité de départ invalide.")

    return dict(name=name, unit=unit, unit_price=unit_price,
                reorder_level=reorder, opening=opening or 0), None


def _set_opening_stock(part, quantity, today):
    """The opening stock is a single 'initial' movement, so editing it just
    adjusts (or removes) that one row. It is deliberately NOT an expense: those
    parts were paid for before the store existed — only a receipt costs money.
    Its unit_price follows the part's indicative price so the opening stock
    still has a value to average.
    """
    mv = next((m for m in part.movements if m.kind == "initial"), None)
    if quantity > 0:
        if mv:
            mv.quantity = quantity
            mv.unit_price = part.unit_price
        else:
            db.session.add(StockMovement(
                part_id=part.id, kind="initial", date=today,
                quantity=quantity, unit_price=part.unit_price,
                created_by=current_user.id))
    elif mv:
        db.session.delete(mv)


_PHOTO_ERR_KEYS = {
    s3_storage.ERR_TOO_LARGE:      "photo.err.too_large",
    s3_storage.ERR_BAD_FORMAT:     "photo.err.bad_format",
    s3_storage.ERR_NOT_CONFIGURED: "photo.err.not_configured",
    s3_storage.ERR_S3:             "photo.err.s3",
    s3_storage.ERR_UNKNOWN:        "photo.err.unknown",
}


def _apply_part_photo_change(part):
    """Optional photo on a part create/edit POST: a new upload replaces (and
    deletes) the previous object, the "remove" box clears it. Returns a
    localized error string when a submitted photo can't be stored, else None.
    Assumes the part row already has an id."""
    t = get_t()
    old_key = part.photo_key
    if request.form.get("photo_remove") == "1":
        part.photo_key = None
    upload = request.files.get("photo")
    if upload and upload.filename:
        key, err = s3_storage.upload_part_photo(upload, part.name)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Photo non enregistrée.")
        part.photo_key = key
    if old_key and old_key != part.photo_key:
        s3_storage.delete_photo(old_key)
    return None


def _render_part_form(part, error=None):
    tpl = "_part_form.html" if is_modal_request() else "part_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, part=part, error=error, units=UNITS), status


@stock_bp.route("/parts/new", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def part_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_part_form(None)
        if error:
            return _render_part_form(None, error)
        opening = data.pop("opening")
        p = Part(created_by=current_user.id, **data)
        db.session.add(p)
        db.session.flush()
        perr = _apply_part_photo_change(p)
        if perr:
            db.session.rollback()
            return _render_part_form(None, perr)
        _set_opening_stock(p, opening, date.today().isoformat())
        log_action("CREATE", "part", resource_id=p.id,
                   detail=f"Created part '{p.name}'")
        db.session.commit()
        flash("success|" + t.get("part.created", "Article créé."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.index"))
    return _render_part_form(None)


@stock_bp.route("/parts/<int:pid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def part_edit(pid):
    part = _get_part_or_404(pid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_part_form(part)
        if error:
            return _render_part_form(part, error)
        opening = data.pop("opening")
        for k, v in data.items():
            setattr(part, k, v)
        perr = _apply_part_photo_change(part)
        if perr:
            db.session.rollback()
            return _render_part_form(part, perr)
        _set_opening_stock(part, opening, date.today().isoformat())
        log_action("UPDATE", "part", resource_id=part.id,
                   detail=f"Edited part '{part.name}'")
        db.session.commit()
        flash("success|" + t.get("part.updated", "Article modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.index"))
    return _render_part_form(part)


# ── Archive / reactivate ─────────────────────────────────────────────────────


@stock_bp.route("/parts/<int:pid>/archive", methods=["POST"])
@login_required
@require_perm("stock.manage")
def part_archive(pid):
    part = _get_part_or_404(pid)
    part.is_active = False
    log_action("ARCHIVE", "part", resource_id=pid,
               detail=f"Archived part '{part.name}'")
    db.session.commit()
    flash("success|" + get_t().get("part.archived", "Article archivé."))
    return redirect(url_for("stock.index"))


@stock_bp.route("/parts/<int:pid>/reactivate", methods=["POST"])
@login_required
@require_perm("stock.manage")
def part_reactivate(pid):
    part = _get_part_or_404(pid)
    part.is_active = True
    log_action("REACTIVATE", "part", resource_id=pid,
               detail=f"Reactivated part '{part.name}'")
    db.session.commit()
    flash("success|" + get_t().get("part.reactivated", "Article réactivé."))
    return redirect(url_for("stock.index", archived=1))


# ── Entrée (parts received) ──────────────────────────────────────────────────


# Payment fields belong to the ledger row, not to the movement — the same
# arrangement the maintenance blueprint uses for a service's cost.
MONEY_KEYS = ("payment_method", "payment_reference")


def split_money(data):
    """Pop the ledger-only fields out of a movement payload. Returns (data, money)."""
    return data, {k: data.pop(k, None) for k in MONEY_KEYS}


def sync_receipt_expense(movement, money):
    """Mirror a receipt into the money ledger as its single 'pieces' row:
    created or updated so the two can never disagree. Deleting the movement
    deletes the row through the relationship's cascade.

    This is the ONE moment parts cost money. Issuing one to a vehicle later
    writes no ledger row at all — it only says who the cost was for. The row
    carries no fleet and no vehicle: a purchase belongs to the company until a
    machine actually consumes it.
    """
    fields = dict(
        vehicle_id=None,
        fleet_id=None,
        label=movement.part.name,
        category=PARTS_CATEGORY,
        date=movement.date,
        amount=movement.value or 0,
        currency="GNF",
        payment_method=money.get("payment_method"),
        payment_reference=money.get("payment_reference"),
        supplier=movement.supplier,
        description=movement.note,
    )
    if movement.expense:
        for k, v in fields.items():
            setattr(movement.expense, k, v)
    else:
        db.session.add(Expense(stock_movement_id=movement.id,
                               created_by=getattr(current_user, "id", None),
                               **fields))


def _read_entree_form():
    """A receipt: quantity in, at the price actually paid that day. The price is
    asked every time — the part's indicative price only pre-fills the field, so
    what a lot really cost is never overwritten by the next one."""
    t = get_t()
    part = db.session.get(Part, request.form.get("part_id", type=int) or 0)
    if not part or not part.is_active:
        return None, t.get("mv.err.part", "Choisissez un article actif.")

    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("mv.err.date", "Date invalide.")

    try:
        quantity = float((request.form.get("quantity") or "").replace(",", "."))
    except ValueError:
        return None, t.get("mv.err.quantity", "Quantité invalide.")
    if quantity <= 0:
        return None, t.get("mv.err.quantity", "Quantité invalide.")

    try:
        unit_price = int(round(float((request.form.get("unit_price") or "").replace(" ", ""))))
    except ValueError:
        return None, t.get("mv.err.price", "Prix invalide.")
    if unit_price < 0:
        return None, t.get("mv.err.price", "Prix invalide.")

    # A receipt becomes a ledger row, so it has to say how it was paid.
    method = (request.form.get("payment_method") or "").strip()
    if method not in PAYMENT_METHODS:
        return None, t.get("expense.err.payment_required",
                           "Choisissez un moyen de paiement.")

    return dict(
        part_id=part.id, kind="entree", date=date_str, quantity=quantity,
        unit_price=unit_price,
        supplier=(request.form.get("supplier") or "").strip() or None,
        payment_method=method,
        payment_reference=(request.form.get("payment_reference") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    ), None


def _render_movement_form(kind, error=None):
    tpl = f"_{kind}_form.html" if is_modal_request() else f"{kind}_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error, parts=_active_parts(),
        payment_methods=PAYMENT_METHODS, today=date.today().isoformat(),
        preset_part=request.args.get("part", type=int)), status


@stock_bp.route("/entree/new", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def entree_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_entree_form()
        if error:
            return _render_movement_form("entree", error)
        data, money = split_money(data)
        mv = StockMovement(created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        sync_receipt_expense(mv, money)
        log_action("CREATE", "stock_movement", resource_id=mv.id,
                   detail=f"Received {mv.quantity} of '{mv.part.name}'")
        db.session.commit()
        flash("success|" + t.get("entree.created", "Réception enregistrée."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.index"))
    return _render_movement_form("entree")


# ── Inventaire (physical count) ──────────────────────────────────────────────


def _read_inventaire_form():
    """A physical count. It resets the running quantity to what was counted, so
    a figure that drifted — including a negative one — gets put right."""
    t = get_t()
    part = db.session.get(Part, request.form.get("part_id", type=int) or 0)
    if not part or not part.is_active:
        return None, t.get("mv.err.part", "Choisissez un article actif.")

    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("mv.err.date", "Date invalide.")

    try:
        quantity = float((request.form.get("quantity") or "").replace(",", "."))
    except ValueError:
        return None, t.get("mv.err.counted", "Quantité comptée invalide.")
    if quantity < 0:
        return None, t.get("mv.err.counted", "Quantité comptée invalide.")

    return dict(part_id=part.id, kind="inventaire", date=date_str,
                quantity=quantity,
                note=(request.form.get("note") or "").strip() or None), None


@stock_bp.route("/inventaire/new", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def inventaire_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_inventaire_form()
        if error:
            return _render_movement_form("inventaire", error)
        mv = StockMovement(created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        gap = mv.ecart
        log_action("CREATE", "stock_movement", resource_id=mv.id,
                   detail=f"Counted {mv.quantity} of '{mv.part.name}'")
        db.session.commit()
        if gap:
            flash("success|" + t.get("inventaire.gap", "Comptage enregistré — écart de %(n)s.")
                  % {"n": f"{abs(gap):,.10g}"})
        else:
            flash("success|" + t.get("inventaire.ok", "Comptage enregistré."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.index"))
    return _render_movement_form("inventaire")


# ── History ──────────────────────────────────────────────────────────────────


@stock_bp.route("/movements")
@login_required
@require_perm("stock.view")
def history():
    filters = {
        "part_id":   request.args.get("part_id", type=int),
        "kind":      request.args.get("kind") or "",
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    q = StockMovement.query
    if filters["part_id"]:
        q = q.filter(StockMovement.part_id == filters["part_id"])
    if filters["kind"] in MOVEMENT_KINDS:
        q = q.filter(StockMovement.kind == filters["kind"])
    if _valid_date(filters["date_from"]):
        q = q.filter(StockMovement.date >= filters["date_from"])
    if _valid_date(filters["date_to"]):
        q = q.filter(StockMovement.date <= filters["date_to"])

    movements = (q.order_by(StockMovement.date.desc(), StockMovement.id.desc())
                 .limit(HISTORY_LIMIT).all())
    return render_template(
        "stock_history.html", movements=movements, filters=filters,
        kinds=MOVEMENT_KINDS, parts=Part.query.order_by(Part.name).all(),
        has_filters=any(filters.values()), limit=HISTORY_LIMIT)


@stock_bp.route("/movements/<int:mid>/delete", methods=["POST"])
@login_required
@require_perm("stock.manage")
def movement_delete(mid):
    mv = _get_movement_or_404(mid)
    t = get_t()
    # Parts issued to a service belong to that record — removing them here
    # would leave the service claiming parts it no longer has.
    if mv.maintenance_record_id:
        flash("error|" + t.get("mv.err.owned_by_record",
                               "Ce mouvement appartient à une fiche d'entretien : "
                               "modifiez-le depuis la fiche."))
        return redirect(request.referrer or url_for("stock.history"))
    if mv.kind == "initial":
        flash("error|" + t.get("mv.err.opening",
                               "Le stock de départ se modifie sur la fiche article."))
        return redirect(request.referrer or url_for("stock.history"))
    name = mv.part.name
    db.session.delete(mv)          # cascades to its ledger row, if any
    log_action("DELETE", "stock_movement", resource_id=mid,
               detail=f"Deleted {mv.kind} of '{name}'")
    db.session.commit()
    flash("success|" + t.get("mv.deleted", "Mouvement supprimé."))
    return redirect(request.referrer or url_for("stock.history"))
