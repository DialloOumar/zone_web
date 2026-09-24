"""Stock blueprint — the workshop store of maintenance parts.

One store for the whole company: unlike vehicles or citernes, a part is not
owned by a fleet, so nothing here is fleet-scoped. What ties a part to a fleet
is the vehicle it ends up on, and that link is carried by the sortie.

A part holds no quantity of its own — what is on hand is computed from its
movements (see Part.quantity_as_of), the same arrangement as a citerne. This
first slice is the catalogue: create, edit, archive, and the opening stock.
What is on the shelf only comes from receiving a purchase order
(blueprints/purchases.py): nothing is typed by hand, no opening quantity,
no receipt, no count. Old movements of those kinds stay readable.

Helpers (require_perm, log_action, get_t, modal helpers) come from app.py; this
module is imported at the bottom of app.py.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from app import (slugify, get_t, is_modal_request, log_action, modal_ok,
                 require_perm)
from models import Part, PartUnit, StockMovement, db

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

# How a part is counted: the store's own list (PartUnit), kept from the
# Unités page. Anything sold by the piece uses "pièce"; oil and grease go by
# the litre or the kilo; a set is 4 tyres bought as one.


def active_units():
    return (PartUnit.query.filter(PartUnit.is_active.is_(True))
            .order_by(PartUnit.sort_order, PartUnit.name).all())


def unit_codes():
    return {u.code for u in PartUnit.query.all()}

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


def _part_usage(part):
    """What would be lost if this part's row went away, so the list can offer an
    outright delete when there is nothing to lose.

    The opening stock does not count: it is part of the part itself, edited on
    its own form, and carries no history of its own. Everything else does — a
    receipt owns a row in the money ledger, and an issue belongs to a service.
    """
    counts = {}
    for kind in ("entree", "sortie", "retour", "inventaire"):
        n = sum(1 for m in part.movements if m.kind == kind)
        if n:
            counts[kind] = n
    return {"counts": counts, "deletable": not counts}


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

    from models import PurchaseOrder
    return render_template(
        "stock.html", parts=parts, show_archived=show_archived, search=search,
        parts_count=Part.query.filter(Part.is_active.is_(True)).count(),
        orders_count=PurchaseOrder.query.count(),
        archived_count=archived_count, total_value=total_value,
        low_count=low_count, recent=recent, active_parts=_active_parts(),
        usage={p.id: _part_usage(p) for p in parts},
        today=date.today().isoformat())


# ── Create / edit ────────────────────────────────────────────────────────────


def _read_part_form(part):
    """Validate the part form. Returns (data, error). No quantity is typed
    here: what is on the shelf only comes from receiving an order."""
    t = get_t()
    name = (request.form.get("name") or "").strip()
    unit = (request.form.get("unit") or "").strip()

    if not name:
        return None, t.get("part.err.name", "La désignation est obligatoire.")
    if unit not in unit_codes():
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

    reorder, bad = _num(request.form.get("reorder_level"), float)
    if bad or (reorder is not None and reorder < 0):
        return None, t.get("part.err.reorder", "Seuil invalide.")

    # A pack needs both a name and a content; one without the other is a slip.
    pack_name = (request.form.get("pack_name") or "").strip()[:40] or None
    pack_size, bad = _num(request.form.get("pack_size"), float)
    if bad or (pack_size is not None and pack_size <= 0) or bool(pack_name) != bool(pack_size):
        return None, t.get("part.err.pack", "Indiquez le nom du conditionnement et son contenu, ou aucun des deux.")

    return dict(name=name, unit=unit, reorder_level=reorder,
                pack_name=pack_name, pack_size=pack_size), None


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
    return render_template(tpl, part=part, error=error, units=active_units()), status


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
        for k, v in data.items():
            setattr(part, k, v)
        perr = _apply_part_photo_change(part)
        if perr:
            db.session.rollback()
            return _render_part_form(part, perr)
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


@stock_bp.route("/parts/<int:pid>/destroy", methods=["POST"])
@login_required
@require_perm("stock.manage")
def part_destroy(pid):
    """Hard delete, allowed only while the part has no movement of its own.

    Re-checked here rather than trusting the button: the list may have been
    rendered before someone logged a receipt against this part. Its opening
    stock goes with it, which is right — that row is the part.
    """
    part = _get_part_or_404(pid)
    t = get_t()
    usage = _part_usage(part)
    if not usage["deletable"]:
        moved = ", ".join(
            "%d %s" % (n, t.get("mv.kind." + k, k).lower())
            for k, n in usage["counts"].items())
        flash("error|" + t.get(
            "part.err.delete_blocked",
            "Impossible de supprimer : cet article a des mouvements (%(moved)s). "
            "Archivez-le à la place.") % {"moved": moved})
        return redirect(request.referrer or url_for("stock.index"))

    name = part.name
    photo_key = part.photo_key
    db.session.delete(part)          # takes its opening movement with it
    log_action("DELETE", "part", resource_id=pid, detail=f"Deleted part '{name}'")
    db.session.commit()
    if photo_key:
        s3_storage.delete_photo(photo_key)
    flash("success|" + t.get("part.deleted", "Article supprimé."))
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


# ── Movement history ─────────────────────────────────────────────────────────
# Read-only: a movement is written by receiving an order or by a service
# record, and is undone from there, so this page offers nothing to delete.


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


# ── The units the store counts in ────────────────────────────────────────────

def _unit_code(name):
    """A code from the name, once, for the parts to carry: "seau" for "Seau",
    "sac_25kg" for "Sac 25 kg". Never shown; the name is."""
    base = slugify(name).replace("-", "_")[:18] or "unite"
    code, n = base, 2
    while PartUnit.query.filter_by(code=code).first():
        code = "%s_%d" % (base[:15], n)
        n += 1
    return code


@stock_bp.route("/unites")
@login_required
@require_perm("stock.manage")
def units():
    return _render_units()


def _render_units(error=None):
    """The units list: as a dialog over the store page when fetched from it,
    as a page of its own otherwise."""
    rows = PartUnit.query.order_by(PartUnit.sort_order, PartUnit.name).all()
    used = dict(db.session.query(Part.unit, db.func.count(Part.id)).group_by(Part.unit).all())
    tpl = "_part_units_modal.html" if is_modal_request() else "part_units.html"
    return render_template(tpl, units=rows, used=used, error=error), (422 if error else 200)


def _render_unit_form(row, error=None):
    # From the dialog, a slip is shown on the list itself, not on a form of
    # its own; the list is where the name was typed.
    if is_modal_request() and request.form.get("_list"):
        return _render_units(error)
    tpl = "_part_unit_form.html" if is_modal_request() else "part_unit_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


def _save_unit(row):
    t = get_t()
    name = (request.form.get("name") or "").strip()[:40]
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = PartUnit.query.filter(db.func.lower(PartUnit.name) == name.lower())
    if row:
        clash = clash.filter(PartUnit.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(PartUnit.sort_order)).scalar() or 0) + 1
        row = PartUnit(code=_unit_code(name), sort_order=nxt)
        db.session.add(row)
    row.name = name
    log_action("CREATE" if creating else "UPDATE", "part_unit", resource_id=row.id,
               detail="%s unit '%s'" % ("Created" if creating else "Renamed", name))
    db.session.commit()
    return None


@stock_bp.route("/unites/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def unit_new():
    if request.method == "POST":
        error = _save_unit(None)
        if error:
            return _render_unit_form(None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.units"))
    return _render_unit_form(None)


@stock_bp.route("/unites/<int:uid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def unit_edit(uid):
    row = db.session.get(PartUnit, uid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_unit(row)
        if error:
            return _render_unit_form(row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("stock.units"))
    return _render_unit_form(row)


@stock_bp.route("/unites/<int:uid>/<any(archive,reactivate,delete):what>", methods=["POST"])
@login_required
@require_perm("stock.manage")
def unit_action(uid, what):
    """Archive takes it out of the pickers and leaves the parts counting in
    it as they are; delete is only for one no part uses."""
    row = db.session.get(PartUnit, uid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if Part.query.filter_by(unit=row.code).count():
            blocked = t.get("list.err.delete_blocked",
                            "Impossible de supprimer : cet élément est utilisé. Archivez-le à la place.")
            if is_modal_request():
                return _render_units(blocked)
            flash("error|" + blocked)
            return redirect(url_for("stock.units"))
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "part_unit", resource_id=uid, detail="Deleted unit '%s'" % name)
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "part_unit", resource_id=uid, detail="%s unit '%s'" % (what.title(), row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    if is_modal_request():
        return modal_ok()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(url_for("stock.units"))
