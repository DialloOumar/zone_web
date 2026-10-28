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
                 parse_amount, require_perm)
from models import Part, PartStoreLevel, PartUnit, PurchaseOrderLine, StockMovement, UnitConversion, db

stock_bp = Blueprint("stock", __name__, url_prefix="/stock")

# How a part is counted: the store's own list (PartUnit), kept from the
# Unités page. Anything sold by the piece uses "pièce"; oil and grease go by
# the litre or the kilo; a set is 4 tyres bought as one.


def active_units():
    return (PartUnit.query.filter(PartUnit.is_active.is_(True))
            .order_by(PartUnit.sort_order, PartUnit.name).all())


def buying_units_for(unit_code):
    """What an order line for a part counting in `unit_code` may be
    written in: every active unit a conversion turns into it. Returns
    (unit, factor) pairs."""
    names = {u.code: u for u in active_units()}
    out = []
    for c in UnitConversion.query.filter_by(to_code=unit_code).all():
        if c.from_code in names:
            out.append((names[c.from_code], c.factor))
    return sorted(out, key=lambda x: (x[0].sort_order, x[0].name))


def unit_codes():
    return {u.code for u in PartUnit.query.all()}

# Kinds the store screen writes directly. "sortie" and "retour" are written by
# a service record, so they are read-only here — they show in the history but
# have no button of their own.
MOVEMENT_KINDS = ("initial", "entree", "sortie", "retour", "inventaire",
                  "transfert_out", "transfert_in")

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


def _part_usage(part):
    """What would be lost if this part's row went away, so the list can offer an
    outright delete when there is nothing to lose.

    The opening stock does not count: it is part of the part itself, edited on
    its own form, and carries no history of its own. Everything else does — a
    receipt owns a row in the money ledger, and an issue belongs to a service.
    """
    counts = {}
    for kind in ("entree", "sortie", "retour", "inventaire", "transfert_out", "transfert_in"):
        n = sum(1 for m in part.movements if m.kind == kind)
        if n:
            counts[kind] = n
    # Its opening stock sitting in a store the user does not run keeps it.
    from blueprints.stores import can_act_in
    if any(m.kind == "initial" and m.store_id and not can_act_in(m.store_id) for m in part.movements):
        counts["initial"] = 1
    return {"counts": counts, "deletable": not counts}


# ── Catalogue ────────────────────────────────────────────────────────────────


@stock_bp.route("/")
@login_required
@require_perm("stock.view")
def index():
    show_archived = request.args.get("archived") == "1"
    search = (request.args.get("q") or "").strip()

    # Which store is shown: one asked for, else the user's own when they run
    # exactly one, else all the ones they see side by side. Someone named on
    # stores sees only those.
    from blueprints.stores import acting_store_ids, visible_store_ids, visible_stores
    stores = visible_stores()
    raw = request.args.get("store") or ""
    mine = acting_store_ids()
    if raw == "all":
        store = None
    elif raw.isdigit():
        store = next((st for st in stores if st.id == int(raw)), None)
    else:
        store = (next((st for st in stores if st.id == mine[0]), None)
                 if mine and len(mine) == 1 else None)
    if len(stores) == 1:
        store = stores[0]
    shown = [store.id] if store else [st.id for st in stores]

    # A store lists only the parts it has had. Whoever sees every store also
    # sees the parts no store has had yet.
    every = visible_store_ids() is None and not store
    def listed(p):
        return every or bool(p.store_ids() & set(shown))

    q = Part.query
    if search:
        q = q.filter(Part.name.ilike(f"%{search}%"))
    candidates = [p for p in q.order_by(Part.name).all() if listed(p)]
    parts = [p for p in candidates if p.is_active == (not show_archived)]
    archived_count = sum(1 for p in candidates if not p.is_active)
    qmap = {p.id: p.quantities for p in parts}

    # Totals over what is shown, so a search narrows them too, each store at
    # its own prices. A part with no price known contributes nothing rather
    # than breaking the sum.
    values = {p.id: {sid: p.value_in(sid) for sid in shown} for p in parts}
    total_value = sum(v or 0 for p in parts for v in values[p.id].values())
    low_count = sum(1 for p in parts if p.below_reorder_among(shown))

    recent = (StockMovement.query.filter(StockMovement.store_id.in_(shown))
              .order_by(StockMovement.date.desc(), StockMovement.id.desc())
              .limit(15).all()) if shown else []

    return render_template(
        "stock.html", parts=parts, show_archived=show_archived, search=search,
        archived_count=archived_count, total_value=total_value,
        low_count=low_count, recent=recent,
        usage={p.id: _part_usage(p) for p in parts},
        stores=stores, store=store, shown=shown, qmap=qmap, values=values,
        today=date.today().isoformat())


# ── Create / edit ────────────────────────────────────────────────────────────


def _read_part_form(part):
    """Validate the part form. Returns (data, error). No quantity is typed
    here: what is on the shelf only comes from receiving an order.

    A new part whose name another store already has is that part, added to
    this store -- as a received order does -- and comes back as `existing`.
    Only this store having it already is refused."""
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
    clash = clash.first()
    existing = None
    if clash and part:
        return None, t.get("part.err.name_taken", "Cette désignation existe déjà.")
    if clash:
        if _opening_store() in clash.store_ids():
            return None, t["part.err.in_store"]
        existing = clash

    def _num(raw, cast):
        raw = (raw or "").replace(" ", "").strip()
        if not raw:
            return None, False
        try:
            return cast(raw), False
        except ValueError:
            return None, True

    from blueprints.stores import acting_store_ids, active_stores
    ids = acting_store_ids()
    levels = {}
    for st in active_stores():
        if ids is not None and st.id not in ids:
            continue
        reorder, bad = _num(request.form.get("reorder_%d" % st.id), float)
        if bad or (reorder is not None and reorder < 0):
            return None, t.get("part.err.reorder", "Seuil invalide.")
        levels[st.id] = reorder

    # Kept for now: what is already on the shelf, and what a unit is worth.
    # Movement fields, not columns of Part: the caller splits them out.
    opening, bad = _num(request.form.get("opening_quantity"), float)
    if bad or (opening is not None and opening < 0):
        return None, t.get("part.err.opening", "Quantité de départ invalide.")
    raw_price = (request.form.get("opening_price") or "").strip()
    opening_price = parse_amount(raw_price) if raw_price else None
    bad = bool(raw_price) and opening_price is None
    if bad or (opening_price is not None and opening_price < 0):
        return None, t.get("part.err.price", "Prix invalide.")

    return dict(name=name, unit=unit, levels=levels, existing=existing,
                opening=opening or 0, opening_price=opening_price), None


def _set_levels(part, levels, store_id=None):
    """Each store's re-order level, for the stores the form showed; another
    store's level is that store's to set. Clearing one leaves the part in
    that store's list. `store_id`, the store the form is for, gets the part
    in its list even with no level."""
    have = {lv.store_id: lv for lv in part.levels}
    for sid, level in levels.items():
        lv = have.get(sid)
        if lv:
            lv.reorder_level = level
        elif level is not None or sid == store_id:
            part.levels.append(PartStoreLevel(store_id=sid, reorder_level=level))


def _opening_movement(part, store_id):
    """A store's opening stock of the part: its 'initial' movement there."""
    return next((m for m in part.movements if m.kind == "initial" and m.store_id == store_id), None)


def _set_opening_stock(part, quantity, price, today, store_id=None):
    """The opening stock is one 'initial' movement per store, so editing it
    just adjusts (or removes) that row. Deliberately NOT an expense: those
    parts were paid for before the store existed."""
    mv = _opening_movement(part, store_id)
    if quantity > 0:
        if mv:
            mv.quantity = quantity
            mv.unit_price = price
        else:
            db.session.add(StockMovement(
                part_id=part.id, kind="initial", date=today, store_id=store_id,
                quantity=quantity, unit_price=price,
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


def _opening_store():
    """The store the opening stock sits in: the one picked, if the user
    works there, else the user's own."""
    from blueprints.stores import default_store_id, store_or_default
    st = store_or_default(request.form.get("opening_store_id", type=int))
    return st.id if st else default_store_id()


def _require_store_worker():
    """Seeing the store is open to every stock role; changing it is for the
    super admin and the people named on a store."""
    from blueprints.stores import works_in_a_store
    if not works_in_a_store():
        abort(403)


def _render_part_form(part, error=None):
    tpl = "_part_form.html" if is_modal_request() else "part_form.html"
    status = 422 if (error and is_modal_request()) else 200
    from blueprints.stores import acting_store_ids, active_stores, default_store_id
    ids = acting_store_ids()
    stores = [st for st in active_stores() if ids is None or st.id in ids]
    # The store the form is for: the tab it was opened from, if the user
    # works there, else their own.
    asked = request.args.get("store", type=int)
    store_id = asked if asked in {st.id for st in stores} else default_store_id()
    return render_template(tpl, part=part, error=error, units=active_units(),
                           opening_stores=stores, level_stores=stores, default_store=store_id,
                           opening_mv=_opening_movement(part, store_id) if part else None), status


@stock_bp.route("/parts/new", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def part_new():
    """Kept for now: an article written straight into the store, with what
    is on the shelf. The usual way in is a received purchase order."""
    _require_store_worker()
    t = get_t()
    if request.method == "POST":
        data, error = _read_part_form(None)
        if error:
            return _render_part_form(None, error)
        opening = data.pop("opening")
        opening_price = data.pop("opening_price")
        levels = data.pop("levels")
        store_id = _opening_store()
        existing = data.pop("existing")
        if existing:
            # Another store has it: the same part, now in this store too.
            existing.is_active = True
            _set_levels(existing, levels, store_id)
            _set_opening_stock(existing, opening, opening_price, date.today().isoformat(), store_id)
            log_action("UPDATE", "part", resource_id=existing.id,
                       detail=f"Added part '{existing.name}' to store #{store_id}")
            db.session.commit()
            flash("success|" + t["part.added"] % existing.name)
            return modal_ok() if is_modal_request() else redirect(url_for("stock.index"))
        p = Part(created_by=current_user.id, **data)
        db.session.add(p)
        _set_levels(p, levels, store_id)
        db.session.flush()
        perr = _apply_part_photo_change(p)
        if perr:
            db.session.rollback()
            return _render_part_form(None, perr)
        _set_opening_stock(p, opening, opening_price, date.today().isoformat(), store_id)
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
    _require_store_worker()
    part = _get_part_or_404(pid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_part_form(part)
        if error:
            return _render_part_form(part, error)
        opening = data.pop("opening")
        opening_price = data.pop("opening_price")
        data.pop("existing")
        _set_levels(part, data.pop("levels"))
        for k, v in data.items():
            setattr(part, k, v)
        perr = _apply_part_photo_change(part)
        if perr:
            db.session.rollback()
            return _render_part_form(part, perr)
        _set_opening_stock(part, opening, opening_price, date.today().isoformat(), _opening_store())
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
    _require_store_worker()
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
    _require_store_worker()
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
    _require_store_worker()
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
        "store_id":  request.args.get("store_id", type=int),
        "kind":      request.args.get("kind") or "",
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    from blueprints.stores import visible_store_ids, visible_stores
    q = StockMovement.query
    ids = visible_store_ids()
    if ids is not None:
        q = q.filter(StockMovement.store_id.in_(ids))
    if filters["part_id"]:
        q = q.filter(StockMovement.part_id == filters["part_id"])
    if filters["store_id"]:
        q = q.filter(StockMovement.store_id == filters["store_id"])
    if filters["kind"] in MOVEMENT_KINDS:
        q = q.filter(StockMovement.kind == filters["kind"])
    if _valid_date(filters["date_from"]):
        q = q.filter(StockMovement.date >= filters["date_from"])
    if _valid_date(filters["date_to"]):
        q = q.filter(StockMovement.date <= filters["date_to"])

    movements = (q.order_by(StockMovement.date.desc(), StockMovement.id.desc())
                 .limit(HISTORY_LIMIT).all())
    # The parts to filter on: the ones the stores seen have had.
    parts = Part.query.order_by(Part.name).all()
    if ids is not None:
        parts = [p for p in parts if p.store_ids() & set(ids)]
    return render_template(
        "stock_history.html", movements=movements, filters=filters,
        kinds=MOVEMENT_KINDS, parts=parts,
        has_filters=any(filters.values()), limit=HISTORY_LIMIT,
        stores=visible_stores())


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
    # In use: the parts counting in it, the order lines bought in it, and
    # the conversions naming it.
    used = dict(db.session.query(Part.unit, db.func.count(Part.id)).group_by(Part.unit).all())
    for code, n in (db.session.query(PurchaseOrderLine.buy_unit, db.func.count(PurchaseOrderLine.id))
                    .filter(PurchaseOrderLine.buy_unit.isnot(None)).group_by(PurchaseOrderLine.buy_unit).all()):
        used[code] = used.get(code, 0) + n
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
# ── Conversions between units ────────────────────────────────────────────────
# "1 fût = 200 litre", kept apart from the units. A conversion lets an order
# line for a part counting in litres be written in fûts; the receipt
# converts back. Kept from a dialog over the store page, like the units.


def _render_conversions(error=None):
    rows = UnitConversion.query.all()
    units = {u.code: u for u in PartUnit.query.all()}
    rows.sort(key=lambda c: (units[c.to_code].name if c.to_code in units else c.to_code,
                             units[c.from_code].name if c.from_code in units else c.from_code))
    # In use: the order lines written in the from-unit for a part counting
    # in the to-unit.
    used = {}
    for c in rows:
        used[c.id] = (db.session.query(db.func.count(PurchaseOrderLine.id))
                      .join(Part, Part.id == PurchaseOrderLine.part_id)
                      .filter(PurchaseOrderLine.buy_unit == c.from_code, Part.unit == c.to_code).scalar() or 0)
    tpl = "_unit_conversions_modal.html" if is_modal_request() else "unit_conversions.html"
    return render_template(tpl, conversions=rows, units=units, used=used,
                           active=active_units(), error=error), (422 if error else 200)


@stock_bp.route("/conversions")
@login_required
@require_perm("stock.manage")
def conversions():
    return _render_conversions()


@stock_bp.route("/conversions/nouvelle", methods=["POST"])
@login_required
@require_perm("stock.manage")
def conversion_new():
    t = get_t()
    frm = (request.form.get("from_code") or "").strip()
    to = (request.form.get("to_code") or "").strip()
    codes = unit_codes()
    if frm not in codes or to not in codes or frm == to:
        return _render_conversions(t["conv.err.units"])
    try:
        factor = float(request.form.get("factor") or "")
    except ValueError:
        factor = 0
    if factor <= 0:
        return _render_conversions(t["conv.err.factor"])
    row = UnitConversion.query.filter_by(from_code=frm, to_code=to).first()
    if row:
        row.factor = factor
        what = "UPDATE"
    else:
        row = UnitConversion(from_code=frm, to_code=to, factor=factor)
        db.session.add(row)
        what = "CREATE"
    db.session.flush()
    log_action(what, "unit_conversion", resource_id=row.id, detail="1 %s = %g %s" % (frm, factor, to))
    db.session.commit()
    flash("success|" + t["conv.saved"])
    return modal_ok() if is_modal_request() else redirect(url_for("stock.conversions"))


@stock_bp.route("/conversions/<int:cid>/supprimer", methods=["POST"])
@login_required
@require_perm("stock.manage")
def conversion_delete(cid):
    """A conversion goes freely: the lines written with it keep the factor
    they were written with."""
    row = db.session.get(UnitConversion, cid)
    if not row:
        abort(404)
    db.session.delete(row)
    log_action("DELETE", "unit_conversion", resource_id=cid, detail="Deleted conversion %s -> %s" % (row.from_code, row.to_code))
    db.session.commit()
    flash("success|" + get_t()["list.deleted"])
    return modal_ok() if is_modal_request() else redirect(url_for("stock.conversions"))


