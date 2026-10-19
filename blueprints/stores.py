"""Stores blueprint -- several stores of parts, each run by its own people.

The catalogue of parts is shared; what is on hand is each store's own,
counted from movements that name their store (see Part.quantities_as_of).

Who works where: a user named on a store (Store.managers) receives, issues
and sends from that store alone, and sees the others read-only. A user with
the stock permission who is named on no store works in all of them -- the
logistics office, the admins.

Parts move between stores by transfer: they leave the sending store at once
and count in the other only when someone there says what actually arrived.
The difference, if any, stays on the transfer for everyone to read.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (get_t, has_perm, is_modal_request, log_action, modal_ok,
                 require_perm)
from blueprints.notifications import notify
from models import Part, Site, StockMovement, StockTransfer, Store, User, db

stores_bp = Blueprint("stores", __name__, url_prefix="/stock")


# ── Who works in which store ─────────────────────────────────────────────────

def active_stores():
    return Store.query.filter(Store.is_active.is_(True)).order_by(Store.name).all()


def acting_store_ids():
    """The stores the current user may receive into, issue from and send
    from. None means all of them; an empty list, none."""
    if not current_user.is_authenticated:
        return []
    if current_user.is_super_admin:
        return None
    mine = [s.id for s in current_user.stores if s.is_active]
    if mine:
        return mine
    return None if has_perm("stock.manage") else []


def can_act_in(store_id):
    ids = acting_store_ids()
    return ids is None or store_id in ids


def runs_every_store():
    """Named on no store and holding the stock permission: the office that
    keeps the stores' list and sees them all alike."""
    return acting_store_ids() is None and has_perm("stock.manage")


def default_store_id():
    """The store a form starts on: the user's own, else the first one."""
    ids = acting_store_ids()
    if ids:
        return ids[0]
    stores = active_stores()
    return stores[0].id if stores else None


def store_or_default(raw_id, need_act=True):
    """A store picked on a form, checked: active, and one the user works in
    when `need_act`. Returns the Store or None."""
    st = db.session.get(Store, raw_id) if raw_id else None
    if st is None or not st.is_active:
        return None
    if need_act and not can_act_in(st.id):
        return None
    return st


# ── The stores' list ─────────────────────────────────────────────────────────

def _require_office():
    if not runs_every_store():
        abort(403)


@stores_bp.route("/magasins")
@login_required
@require_perm("stock.view")
def index():
    stores = Store.query.order_by(Store.is_active.desc(), Store.name).all()
    in_use = {}
    for st in stores:
        in_use[st.id] = (StockMovement.query.filter_by(store_id=st.id).count()
                         + StockTransfer.query.filter(db.or_(StockTransfer.from_store_id == st.id,
                                                             StockTransfer.to_store_id == st.id)).count())
    return render_template("stores.html", stores=stores, in_use=in_use,
                           can_edit=runs_every_store())


def _render_store_form(store, error=None):
    tpl = "_store_form.html" if is_modal_request() else "store_form.html"
    status = 422 if (error and is_modal_request()) else 200
    users = User.query.filter(User.is_active.is_(True)).order_by(User.full_name).all()
    sites = Site.query.filter(Site.is_active.is_(True)).order_by(Site.sort_order, Site.name).all()
    return render_template(tpl, store=store, error=error, users=users, sites=sites), status


def _save_store(store):
    t = get_t()
    name = (request.form.get("name") or "").strip()[:80]
    if not name:
        return None, t["store.err.name"]
    clash = Store.query.filter(db.func.lower(Store.name) == name.lower())
    if store:
        clash = clash.filter(Store.id != store.id)
    if clash.first():
        return None, t["store.err.name_taken"]
    site_id = request.form.get("site_id", type=int) or None
    if site_id and not db.session.get(Site, site_id):
        site_id = None
    ids = {int(x) for x in request.form.getlist("manager_ids") if x.isdigit()}
    managers = User.query.filter(User.id.in_(ids)).all() if ids else []
    creating = store is None
    if creating:
        store = Store()
        db.session.add(store)
    store.name, store.site_id, store.managers = name, site_id, managers
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "store", resource_id=store.id,
               detail="%s store '%s' (%d manager(s))" % ("Created" if creating else "Edited",
                                                          name, len(managers)))
    return store, None


@stores_bp.route("/magasins/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def store_new():
    _require_office()
    if request.method == "POST":
        store, error = _save_store(None)
        if error:
            return _render_store_form(None, error)
        db.session.commit()
        flash("success|" + get_t()["store.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("stores.index"))
    return _render_store_form(None)


@stores_bp.route("/magasins/<int:sid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def store_edit(sid):
    _require_office()
    store = db.session.get(Store, sid) or abort(404)
    if request.method == "POST":
        store, error = _save_store(store)
        if error:
            return _render_store_form(db.session.get(Store, sid), error)
        db.session.commit()
        flash("success|" + get_t()["store.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("stores.index"))
    return _render_store_form(store)


@stores_bp.route("/magasins/<int:sid>/<any(archive,reactivate):what>", methods=["POST"])
@login_required
@require_perm("stock.manage")
def store_action(sid, what):
    """Archive takes a store out of the pickers; what it holds stays counted
    and readable. A store still holding parts cannot be archived."""
    _require_office()
    store = db.session.get(Store, sid) or abort(404)
    t = get_t()
    if what == "archive":
        held = sum(1 for p in Part.query.all() if p.quantity_in(store.id) > 1e-9)
        transit = StockTransfer.query.filter(StockTransfer.status == "transit",
                                             db.or_(StockTransfer.from_store_id == sid,
                                                    StockTransfer.to_store_id == sid)).count()
        if held or transit:
            flash("error|" + t["store.err.not_empty"])
            return redirect(url_for("stores.index"))
        if len(active_stores()) <= 1:
            flash("error|" + t["store.err.last"])
            return redirect(url_for("stores.index"))
    store.is_active = what == "reactivate"
    log_action("ARCHIVE" if what == "archive" else "REACTIVATE", "store", resource_id=sid,
               detail="%s store '%s'" % ("Archived" if what == "archive" else "Reactivated", store.name))
    db.session.commit()
    flash("success|" + t["store.archived" if what == "archive" else "store.reactivated"])
    return redirect(url_for("stores.index"))


# ── Transfers ────────────────────────────────────────────────────────────────

def _valid_date(s):
    try:
        datetime.strptime(s or "", "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _qty(raw):
    try:
        v = float((raw or "").strip().replace(",", "."))
    except ValueError:
        return None
    return v


def transfers_to_receive():
    """Transfers waiting in a store the current user is named on -- or, for
    the office, in a store nobody is named on."""
    if not current_user.is_authenticated:
        return []
    mine = [s.id for s in current_user.stores if s.is_active]
    q = StockTransfer.query.filter_by(status="transit")
    if mine:
        q = q.filter(StockTransfer.to_store_id.in_(mine))
    elif runs_every_store():
        unmanned = [s.id for s in active_stores() if not s.managers]
        if not unmanned:
            return []
        q = q.filter(StockTransfer.to_store_id.in_(unmanned))
    else:
        return []
    return q.order_by(StockTransfer.sent_date, StockTransfer.id).all()


@stores_bp.route("/transferts")
@login_required
@require_perm("stock.view")
def transfers():
    status = request.args.get("status") or "transit"
    q = StockTransfer.query
    if status in ("transit", "received"):
        q = q.filter(StockTransfer.status == status)
    store_id = request.args.get("store", type=int)
    if store_id:
        q = q.filter(db.or_(StockTransfer.from_store_id == store_id,
                            StockTransfer.to_store_id == store_id))
    rows = q.order_by(StockTransfer.sent_date.desc(), StockTransfer.id.desc()).limit(300).all()
    counts = dict(transit=StockTransfer.query.filter_by(status="transit").count())
    return render_template("stock_transfers.html", rows=rows, status=status, counts=counts,
                           stores=active_stores(), store_id=store_id,
                           can_send=bool(acting_store_ids() is None or acting_store_ids()) and has_perm("stock.manage")
                                    and len(active_stores()) > 1)


def _render_transfer_form(error=None):
    tpl = "_transfer_form.html" if is_modal_request() else "transfer_form.html"
    status = 422 if (error and is_modal_request()) else 200
    ids = acting_store_ids()
    stores = active_stores()
    from_stores = [s for s in stores if ids is None or s.id in ids]
    parts = Part.query.filter(Part.is_active.is_(True)).order_by(Part.name).all()
    # What each part holds in each store, for the form to show as it is picked.
    held = {p.id: {str(k): v for k, v in p.quantities.items()} for p in parts}
    return render_template(tpl, error=error, stores=stores, from_stores=from_stores,
                           parts=parts, held=held, default_from=default_store_id(),
                           preset_part=request.args.get("part_id", type=int),
                           today=date.today().isoformat()), status


@stores_bp.route("/transferts/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def transfer_new():
    t = get_t()
    if request.method == "POST":
        src = store_or_default(request.form.get("from_store_id", type=int))
        dst = store_or_default(request.form.get("to_store_id", type=int), need_act=False)
        part = db.session.get(Part, request.form.get("part_id", type=int) or 0)
        qty = _qty(request.form.get("quantity"))
        day = (request.form.get("date") or "").strip() or date.today().isoformat()
        if not src:
            return _render_transfer_form(t["stransfer.err.from"])
        if not dst or dst.id == src.id:
            return _render_transfer_form(t["stransfer.err.to"])
        if not part or not part.is_active:
            return _render_transfer_form(t["stransfer.err.part"])
        if qty is None or qty <= 0:
            return _render_transfer_form(t["stransfer.err.qty"])
        if qty > part.quantity_in(src.id) + 1e-9:
            return _render_transfer_form(t["stransfer.err.more_than_held"] % (
                "{:.10g}".format(part.quantity_in(src.id))))
        if not _valid_date(day):
            return _render_transfer_form(t.get("client.err.date", "Date invalide."))
        out = StockMovement(part_id=part.id, store_id=src.id, kind="transfert_out", date=day,
                            quantity=qty, unit_value=part.average_cost_as_of(day),
                            note=dst.name, created_by=current_user.id)
        db.session.add(out)
        db.session.flush()
        tr = StockTransfer(part_id=part.id, from_store_id=src.id, to_store_id=dst.id,
                           quantity=qty, sent_date=day, sent_by=current_user.id,
                           note=(request.form.get("note") or "").strip()[:255] or None,
                           out_movement_id=out.id)
        db.session.add(tr)
        db.session.flush()
        for u in dst.managers:
            notify(u.id, "info.stock_transfer_sent", url_for("stores.transfers"),
                   qty="{:.10g}".format(qty), part=part.name, store=src.name)
        log_action("CREATE", "stock_transfer", resource_id=tr.id,
                   detail="Sent %g x '%s' from %s to %s" % (qty, part.name, src.name, dst.name))
        db.session.commit()
        flash("success|" + t["stransfer.sent"])
        return modal_ok() if is_modal_request() else redirect(url_for("stores.transfers"))
    return _render_transfer_form()


def _get_transfer(tid):
    tr = db.session.get(StockTransfer, tid)
    if not tr:
        abort(404)
    return tr


def _render_receive_form(tr, error=None):
    tpl = "_transfer_receive.html" if is_modal_request() else "transfer_receive.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, tr=tr, error=error, today=date.today().isoformat()), status


@stores_bp.route("/transferts/<int:tid>/reception", methods=["GET", "POST"])
@login_required
@require_perm("stock.manage")
def transfer_receive(tid):
    """The receiving store says what actually came: that, and only that,
    enters its stock."""
    tr = _get_transfer(tid)
    t = get_t()
    if tr.status != "transit":
        flash("error|" + t["stransfer.err.done"])
        return modal_ok() if is_modal_request() else redirect(url_for("stores.transfers"))
    if not can_act_in(tr.to_store_id):
        abort(403)
    if request.method == "POST":
        qty = _qty(request.form.get("received_quantity"))
        day = (request.form.get("date") or "").strip() or date.today().isoformat()
        note = (request.form.get("received_note") or "").strip()[:255] or None
        if qty is None or qty < 0 or qty > tr.quantity + 1e-9:
            return _render_receive_form(tr, t["stransfer.err.received_qty"])
        if not _valid_date(day) or day < tr.sent_date:
            return _render_receive_form(tr, t["stransfer.err.received_date"])
        if qty < tr.quantity - 1e-9 and not note:
            return _render_receive_form(tr, t["stransfer.err.gap_note"])
        if qty > 0:
            mv = StockMovement(part_id=tr.part_id, store_id=tr.to_store_id, kind="transfert_in",
                               date=day, quantity=qty,
                               unit_value=tr.out_movement.unit_value if tr.out_movement else None,
                               note=tr.from_store.name, created_by=current_user.id)
            db.session.add(mv)
            db.session.flush()
            tr.in_movement_id = mv.id
        tr.status, tr.received_quantity, tr.received_date = "received", qty, day
        tr.received_by, tr.received_note = current_user.id, note
        kind = "info.stock_transfer_gap" if tr.gap and tr.gap > 1e-9 else "info.stock_transfer_received"
        notify(tr.sent_by, kind, url_for("stores.transfers", status="received"),
               qty="{:.10g}".format(qty), sent="{:.10g}".format(tr.quantity),
               part=tr.part.name, store=tr.to_store.name)
        log_action("RECEIVE", "stock_transfer", resource_id=tr.id,
                   detail="Received %g of %g x '%s' in %s" % (qty, tr.quantity, tr.part.name, tr.to_store.name))
        db.session.commit()
        flash("success|" + t["stransfer.received"])
        return modal_ok() if is_modal_request() else redirect(url_for("stores.transfers"))
    return _render_receive_form(tr)


@stores_bp.route("/transferts/<int:tid>/annuler", methods=["POST"])
@login_required
@require_perm("stock.manage")
def transfer_cancel(tid):
    """Called back before the other store confirmed: the parts never left."""
    tr = _get_transfer(tid)
    t = get_t()
    if tr.status != "transit" or not can_act_in(tr.from_store_id):
        flash("error|" + t["stransfer.err.done"])
        return redirect(url_for("stores.transfers"))
    out = tr.out_movement
    db.session.delete(tr)
    if out:
        db.session.delete(out)
    log_action("DELETE", "stock_transfer", resource_id=tid, detail="Cancelled transfer #%d" % tid)
    db.session.commit()
    flash("success|" + t["stransfer.cancelled"])
    return redirect(url_for("stores.transfers"))


@stores_bp.app_context_processor
def _store_helpers():
    def waiting():
        try:
            return StockTransfer.query.filter_by(status="transit").count()
        except Exception:
            return 0
    return dict(can_act_in_store=can_act_in, runs_every_store=runs_every_store,
                transfers_waiting=waiting)
