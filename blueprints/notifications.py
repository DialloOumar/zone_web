"""The bell: what waits for the person looking, and what happened to them.

Two kinds, never mixed up:

  • À faire -- work waiting for this person's hand: an order to sign, a
    change to approve, parts the cash box has to pay. Read off the work
    itself on every page, never stored, so it is gone the moment anyone who
    may do it does it. Each goes only to whoever may act on it: the holder
    of the role's signature box, the fleet's approver, the cashier.

  • Pour info -- something that happened to one person: their order was
    approved or refused, their request answered, the box paid for their
    parts. Stored for them alone, kept until read.

Nobody is told about what they did themselves.
"""
from datetime import datetime

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import get_t, has_perm
from models import (Notification, PendingChange, PurchaseOrder, ServicePartPurchase, db)

notifications_bp = Blueprint("notifications", __name__)

# How many of the read ones the page keeps showing.
READ_SHOWN = 30


def notify(user_id, kind, url=None, **params):
    """Tell one person that something happened to them. Not the person who
    did it, and nobody when the work has no owner. The caller commits."""
    if not user_id or (current_user.is_authenticated and user_id == current_user.id):
        return
    db.session.add(Notification(user_id=user_id, kind=kind, url=url, params=params or None))


def _holds(flag):
    """Whether one of the current user's roles carries this box. The super
    admin holds every one."""
    if current_user.is_super_admin:
        return True
    return any(uf.role and getattr(uf.role, flag, False) for uf in current_user.user_fleets)


def todos():
    """What waits for the current user, as lines of the bell: each with its
    text key, what fills it, and where to go to do it."""
    if not current_user.is_authenticated:
        return []
    out = []

    # Orders to sign: logistics first, then finance -- each to whoever holds
    # that box. Finance signs from Facturation when they can open it.
    if _holds("approves_logistics"):
        for po in (PurchaseOrder.query.filter_by(status="pending_logistics")
                   .order_by(PurchaseOrder.date, PurchaseOrder.id).all()):
            out.append(dict(kind="todo.po_logistics", url=url_for("purchases.detail", oid=po.id),
                            params=dict(number=po.number, supplier=po.supplier.name if po.supplier else "")))
    if _holds("approves_finance"):
        on_billing = has_perm("invoicing.view")
        for po in (PurchaseOrder.query.filter_by(status="pending_finance")
                   .order_by(PurchaseOrder.date, PurchaseOrder.id).all()):
            url = (url_for("invoicing.index", tab="commandes") if on_billing
                   else url_for("purchases.detail", oid=po.id))
            out.append(dict(kind="todo.po_finance", url=url,
                            params=dict(number=po.number, supplier=po.supplier.name if po.supplier else "")))

    # Changes waiting for an approver of their fleet -- never one's own.
    if current_user.is_super_admin:
        fleets = None
    else:
        fleets = [uf.fleet_id for uf in current_user.user_fleets if uf.role and uf.role.can_approve]
    if fleets is None or fleets:
        q = PendingChange.query.filter(PendingChange.status == "pending",
                                       PendingChange.requested_by != current_user.id)
        if fleets is not None:
            q = q.filter(PendingChange.fleet_id.in_(fleets))
        n = q.count()
        if n:
            out.append(dict(kind="todo.approvals", url=url_for("approvals.index"), params=dict(n=n)))

    # Parts bought outside for a service, for whoever pays from the box.
    if has_perm("expense.create"):
        n = ServicePartPurchase.query.filter_by(state="pending").count()
        if n:
            out.append(dict(kind="todo.caisse", url=url_for("expenses.index", tab="a_regler"),
                            params=dict(n=n)))
    return out


def unread():
    if not current_user.is_authenticated:
        return []
    return (Notification.query.filter_by(user_id=current_user.id, read_at=None)
            .order_by(Notification.created_at.desc()).all())


def text(kind, params):
    """A line of the bell in the reader's language."""
    tpl = get_t().get("notif." + kind, kind)
    try:
        return tpl % (params or {})
    except (KeyError, TypeError, ValueError):
        return tpl


def bell():
    """What the top bar needs on every page: the lines and the count."""
    if not current_user.is_authenticated:
        return dict(todos=[], unread=[], count=0)
    td, un = todos(), unread()
    return dict(todos=td, unread=un, count=len(td) + len(un))


@notifications_bp.route("/notifications")
@login_required
def index():
    read = (Notification.query.filter(Notification.user_id == current_user.id,
                                      Notification.read_at.isnot(None))
            .order_by(Notification.created_at.desc()).limit(READ_SHOWN).all())
    return render_template("notifications.html", todo_lines=todos(), unread_lines=unread(),
                           read_lines=read)


@notifications_bp.route("/notifications/compte")
@login_required
def count():
    """What the page asks every 30 seconds while it is looked at: only the
    number, a few bytes, so a slow connection hardly notices."""
    resp = jsonify(count=bell()["count"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@notifications_bp.route("/notifications/panneau")
@login_required
def panel():
    """The bell's list, drawn fresh when the bell is opened. `from` is the
    page it was opened on, where "mark all read" comes back to."""
    back = request.args.get("from") or ""
    if not (back.startswith("/") and not back.startswith("//")):
        back = url_for("notifications.index")
    resp = render_template("_bell_panel.html", b=bell(), back_path=back)
    return resp, 200, {"Cache-Control": "no-store"}


@notifications_bp.route("/notifications/<int:nid>")
@login_required
def open_one(nid):
    """Opening one marks it read and goes where it points."""
    n = db.session.get(Notification, nid)
    if not n or n.user_id != current_user.id:
        abort(404)
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(n.url or url_for("notifications.index"))


@notifications_bp.route("/notifications/tout-lu", methods=["POST"])
@login_required
def read_all():
    (Notification.query.filter_by(user_id=current_user.id, read_at=None)
     .update({"read_at": datetime.utcnow()}, synchronize_session=False))
    db.session.commit()
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//")
                    else url_for("notifications.index"))
