"""Approvals blueprint — review queue for parked PendingChange rows.

When a user's role marks an action as "Approval", the feature modules call
submit_change() to park the proposed state here instead of applying it. An
approver (a user whose role has can_approve on the fleet, or the super admin)
reviews the queue and approves (replays the change with its side-effects) or
rejects it.
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import maintenance_engine
from app import current_user_fleet_ids, get_t, log_action
from blueprints.entries import _recompute_cumulatives
from blueprints.expenses import sync_account_movement
from blueprints.maintenance import (MONEY_KEYS, PARTS_KEY, _close_alert_for_record,
                                    sync_record_parts, sync_service_expense)
from models import (DailyEntry, Expense, Fleet, MaintenanceRecord, Operator,
                    Part, PendingChange, User, Vehicle, VehicleCategory, db)

approvals_bp = Blueprint("approvals", __name__)

# resource_type -> (Model, creator attribute set to the original requester)
RESOURCE_MODELS = {
    "vehicle": (Vehicle, "created_by"),
    "operator": (Operator, "created_by"),
    "daily_entry": (DailyEntry, "created_by"),
    "expense": (Expense, "created_by"),
    "maintenance_record": (MaintenanceRecord, "recorded_by"),
}


def _approver_fleet_ids():
    """Fleet ids the current user may approve for; None = super admin (all)."""
    if current_user.is_super_admin:
        return None
    return [uf.fleet_id for uf in current_user.user_fleets
            if uf.role and uf.role.can_approve]


def _require_approver():
    ids = _approver_fleet_ids()
    if ids is not None and not ids:
        abort(403)
    return ids


def _describe_target(pc):
    """For a delete, the payload is empty — summarise the existing record that
    would be removed so the approver can see what they're signing off on."""
    Model, _ = RESOURCE_MODELS[pc.resource_type]
    obj = db.session.get(Model, pc.resource_id)
    if obj is None:
        return []
    rows = []
    if pc.resource_type == "daily_entry":
        v = db.session.get(Vehicle, obj.vehicle_id)
        rows.append(("date", obj.date))
        rows.append(("véhicule", v.code if v else obj.vehicle_id))
        if obj.trips is not None:
            rows.append(("voyages", obj.trips))
        if obj.hours is not None:
            rows.append(("heures", obj.hours))
        if obj.kilometers:
            rows.append(("km", obj.kilometers))
        if obj.operator:
            rows.append(("conducteur", obj.operator))
    else:
        for attr in ("code", "name", "date", "type"):
            if getattr(obj, attr, None):
                rows.append((attr, getattr(obj, attr)))
    return rows


# Friendly French labels for payload keys (the whole describe layer is FR-only,
# matching the hardcoded "véhicule"/"flotte" labels below).
_FIELD_LABELS = {
    "date": "date", "hours": "heures", "trips": "voyages", "kilometers": "km",
    "operator": "conducteur", "note": "note", "index_start": "index début",
    "index_end": "index fin", "cost": "coût", "supplier": "fournisseur",
    "description": "description", "amount": "montant", "liters": "litres",
    "type": "type", "name": "nom", "code": "code", "parts": "pièces",
    "account": "payé par", "site": "site",
}


def _describe(pc):
    """Render the change as readable (label, value) rows, resolving FK ids.
    Create/update describe the proposed payload; delete summarises the target."""
    if pc.action == "delete":
        return _describe_target(pc)
    out = []
    for k, v in (pc.payload or {}).items():
        if v is None or v == "":
            continue
        label = _FIELD_LABELS.get(k, k.replace("_id", "").replace("_", " "))
        val = v
        if k == "parts":
            # A list of {part_id, quantity} — name the parts instead of dumping
            # the raw payload at the approver.
            names = []
            for line in v:
                o = db.session.get(Part, line.get("part_id"))
                qty = line.get("quantity")
                names.append(f"{o.name if o else line.get('part_id')} × {qty:.10g}")
            val = ", ".join(names)
        elif k == "vehicle_id":
            o = db.session.get(Vehicle, v)
            val = o.code if o else v
            label = "véhicule"
        elif k == "fleet_id":
            o = db.session.get(Fleet, v)
            val = o.name if o else v
            label = "flotte"
        elif k == "category_id":
            o = db.session.get(VehicleCategory, v)
            val = o.label if o else v
            label = "catégorie"
        elif isinstance(v, bool):
            val = "Oui" if v else "Non"
        out.append((label, val))
    return out


def _apply(pc):
    """Replay an approved change. Returns True on success."""
    Model, creator_attr = RESOURCE_MODELS[pc.resource_type]
    payload = dict(pc.payload or {})
    obj = None
    vehicle_id = None  # affected vehicle for daily_entry side-effects
    # A service's cost and its parts ride along in the payload but belong to the
    # ledger and to the store, so they are set aside here and re-applied once the
    # record itself exists.
    is_service = pc.resource_type == "maintenance_record"
    money = ({k: payload.pop(k, None) for k in MONEY_KEYS} if is_service else None)
    parts = (payload.pop(PARTS_KEY, None) or []) if is_service else []
    if pc.action == "create":
        obj = Model(**payload)
        setattr(obj, creator_attr, pc.requested_by)
        db.session.add(obj)
        db.session.flush()
    elif pc.action == "update":
        obj = db.session.get(Model, pc.resource_id)
        if not obj:
            return False
        for k, v in payload.items():
            setattr(obj, k, v)
    elif pc.action == "delete":
        target = db.session.get(Model, pc.resource_id)
        if target:
            if pc.resource_type == "daily_entry":
                vehicle_id = target.vehicle_id  # capture before the row is gone
            db.session.delete(target)
        db.session.flush()
    else:
        return False

    # Side-effects mirroring the feature blueprints.
    if pc.resource_type == "daily_entry":
        if obj is not None:
            vehicle_id = obj.vehicle_id
        if vehicle_id is not None:
            _recompute_cumulatives(vehicle_id)
            maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, vehicle_id))
    if pc.resource_type == "expense" and obj is not None:
        # A cost an account paid carries a money-in beside it, written here too
        # so an approved cost lands the same way one entered directly does.
        db.session.flush()
        sync_account_movement(obj)
    if pc.resource_type == "maintenance_record" and obj is not None:
        db.session.flush()
        sync_service_expense(obj, money or {})
        sync_record_parts(obj, parts)
        if pc.action == "create":
            _close_alert_for_record(obj)
        maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, obj.vehicle_id))
    return True


@approvals_bp.route("/approvals")
@login_required
def index():
    ids = _require_approver()
    show = request.args.get("status") or "pending"
    q = PendingChange.query
    if ids is not None:
        q = q.filter(PendingChange.fleet_id.in_(ids))
    if show in ("pending", "approved", "rejected"):
        q = q.filter(PendingChange.status == show)
    rows = q.order_by(PendingChange.requested_at.desc()).limit(200).all()

    requesters = {u.id: u for u in User.query.all()}
    fleets = {f.id: f for f in Fleet.query.all()}
    items = [{"pc": pc, "fields": _describe(pc),
              "requester": requesters.get(pc.requested_by),
              "fleet": fleets.get(pc.fleet_id)} for pc in rows]

    pending_q = PendingChange.query.filter_by(status="pending")
    if ids is not None:
        pending_q = pending_q.filter(PendingChange.fleet_id.in_(ids))
    return render_template("approvals.html", items=items, show=show,
                           pending_count=pending_q.count())


@approvals_bp.route("/mes-demandes")
@login_required
def my_requests():
    """The requester's own submissions and their status — read-only."""
    show = request.args.get("status") or "pending"
    q = PendingChange.query.filter_by(requested_by=current_user.id)
    if show in ("pending", "approved", "rejected"):
        q = q.filter(PendingChange.status == show)
    rows = q.order_by(PendingChange.requested_at.desc()).limit(200).all()
    fleets = {f.id: f for f in Fleet.query.all()}
    items = [{"pc": pc, "fields": _describe(pc), "fleet": fleets.get(pc.fleet_id)}
             for pc in rows]
    pending_count = PendingChange.query.filter_by(
        requested_by=current_user.id, status="pending").count()
    return render_template("my_requests.html", items=items, show=show,
                           pending_count=pending_count)


@approvals_bp.route("/approvals/<int:pid>/review", methods=["POST"])
@login_required
def review(pid):
    ids = _require_approver()
    pc = db.session.get(PendingChange, pid)
    if not pc:
        abort(404)
    if ids is not None and pc.fleet_id not in ids:
        abort(403)
    t = get_t()
    if pc.status != "pending":
        flash("error|" + t["approval.already"])
        return redirect(url_for("approvals.index"))

    action = request.form.get("action")
    note = (request.form.get("note") or "").strip() or None
    if action == "approve":
        if not _apply(pc):
            flash("error|" + t["approval.apply_failed"])
            return redirect(url_for("approvals.index"))
        pc.status = "approved"
        log_action("APPROVE", "pending_change", resource_id=pc.id, fleet_id=pc.fleet_id,
                   detail="Approved %s %s" % (pc.action, pc.resource_type))
        flash("success|" + t["approval.approved"])
    elif action == "reject":
        pc.status = "rejected"
        log_action("REJECT", "pending_change", resource_id=pc.id, fleet_id=pc.fleet_id,
                   detail="Rejected %s %s" % (pc.action, pc.resource_type))
        flash("success|" + t["approval.rejected"])
    else:
        abort(400)
    pc.reviewed_by = current_user.id
    pc.reviewed_at = datetime.utcnow()
    pc.review_note = note
    db.session.commit()
    return redirect(url_for("approvals.index"))
