"""Stores blueprint -- several stores of parts, each its own.

The names of the parts are common to every store; what is on hand, what it
cost and the level to re-order at are each store's own, counted from
movements that name their store (see Part.quantities_as_of). A store shows
only the parts it has had.

Who works where: a user named on a store (Store.managers) receives and issues
from that store alone, and sees only the stores he is named on. A user named
on no store sees every store and works in none. Only the super admin works in
all of them, and only he creates the stores and names who runs them: the
stores' list is his page, Configuration du stock, under Administration.

Parts are not sent from one store to another. The transfers of before stay
in the history of the stores they moved.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import get_t, is_modal_request, log_action, modal_ok, require_perm
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
    return [s.id for s in current_user.stores if s.is_active]


def can_act_in(store_id):
    ids = acting_store_ids()
    return ids is None or store_id in ids


def visible_store_ids():
    """The stores the current user may see. Someone named on stores sees
    those alone; the super admin and the office, named on none, see all
    (None)."""
    if not current_user.is_authenticated:
        return []
    if current_user.is_super_admin:
        return None
    mine = [s.id for s in current_user.stores if s.is_active]
    return mine or None


def visible_stores():
    ids = visible_store_ids()
    return [st for st in active_stores() if ids is None or st.id in ids]


def runs_every_store():
    """The super admin: he works in every store, keeps the stores' list and
    names who runs each one. Nobody else does, whatever their role."""
    return bool(current_user.is_authenticated and current_user.is_super_admin)


def works_in_a_store():
    """Whether the user may change anything in the store at all: the super
    admin, or someone named on at least one store. Everyone else reads."""
    ids = acting_store_ids()
    return ids is None or bool(ids)


def default_store_id():
    """The store a form starts on: the user's own; for the super admin, the
    first one; for someone who works in none, none."""
    ids = acting_store_ids()
    if ids:
        return ids[0]
    if ids is None:
        stores = active_stores()
        return stores[0].id if stores else None
    return None


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
    _require_office()
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
        if held:
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


@stores_bp.app_context_processor
def _store_helpers():
    def receipts_waiting():
        try:
            from blueprints.stock import receipts_waiting_query
            return receipts_waiting_query().count()
        except Exception:
            return 0
    return dict(can_act_in_store=can_act_in, runs_every_store=runs_every_store,
                works_in_a_store=works_in_a_store, receipts_waiting=receipts_waiting)
