"""Maintenance blueprint — rules, records and the alert centre.

- Rules (config): recurring km / hours / time triggers, targeting a vehicle,
  category, fleet or all vehicles. Permission-gated, not approval-routed.
- Records (operational): a service that actually happened; logging one against
  a rule resets that rule's counter and resolves its open alert. Approval/grace
  routed like other operational data.
- Alerts: the open/snoozed queue produced by the engine, with snooze / resolve
  / dismiss. The alert centre lazily re-evaluates rules on load (stand-in for a
  nightly cron, which is deferred).
"""
from datetime import datetime, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import maintenance_engine
from app import (current_user_categories, current_user_fleet_ids, get_t,
                 is_modal_request, log_action, modal_ok, needs_approval,
                 require_perm, scoped, submit_change, with_current_fleet)
from blueprints.expenses import MAINTENANCE_CATEGORY, PAYMENT_METHODS
from models import (Alert, Expense, Fleet, MaintenanceRecord, MaintenanceRule,
                    Operator, Part, StockMovement, Vehicle, VehicleCategory, db)

maintenance_bp = Blueprint("maintenance", __name__)

# "time_recurring" (calendar-based) is hidden from the form for now — the engine
# still evaluates any rule already saved with that type.
RULE_TYPES = ["km_recurring", "hours_recurring", "trips_recurring"]
RECORD_TYPES = ["oil_change", "filter", "tires", "brakes", "repair", "parts",
                "revision", "other"]
SEVERITIES = ["info", "warning", "critical"]
SNOOZE_DAYS = 7


# ── Shared helpers ───────────────────────────────────────────────────────────


def _accessible_fleets():
    fids = current_user_fleet_ids()
    # Archived fleets drop out of the pickers (no new data on a mothballed
    # fleet); existing data stays visible, scoped by current_user_fleet_ids.
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _accessible_vehicles():
    fids = current_user_fleet_ids()
    q = Vehicle.query
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _accessible_operators():
    q = Operator.query.filter_by(is_active=True)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Operator.fleet_id.in_(fids))
    return q.order_by(Operator.name).all()


def _accessible_categories():
    codes = current_user_categories()  # None = super admin
    q = VehicleCategory.query.order_by(VehicleCategory.sort_order)
    if codes is not None:
        q = q.filter(VehicleCategory.code.in_(codes))
    return q.all()


def _num(raw, cast):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None, False
    try:
        return cast(raw), False
    except (TypeError, ValueError):
        return None, True


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ── Rules ────────────────────────────────────────────────────────────────────


def _rule_accessible(rule):
    fids = current_user_fleet_ids()
    if fids is None:
        return True
    if rule.all_vehicles:
        return True
    if rule.fleet_id and rule.fleet_id in fids:
        return True
    if rule.vehicle_id:
        v = db.session.get(Vehicle, rule.vehicle_id)
        return bool(v and v.fleet_id in fids)
    if rule.category_id:
        codes = current_user_categories() or []
        c = db.session.get(VehicleCategory, rule.category_id)
        return bool(c and c.code in codes)
    return False


def _rule_target_value(rule):
    if rule.all_vehicles:
        return "all"
    if rule.fleet_id:
        return f"fleet:{rule.fleet_id}"
    if rule.category_id:
        return f"category:{rule.category_id}"
    if rule.vehicle_id:
        return f"vehicle:{rule.vehicle_id}"
    return "all"


def _parse_target(raw):
    base = dict(all_vehicles=False, fleet_id=None, category_id=None, vehicle_id=None)
    fids = current_user_fleet_ids()
    if raw == "all":
        base["all_vehicles"] = True
        return base
    if ":" in raw:
        kind, sid = raw.split(":", 1)
        if not sid.isdigit():
            return None
        sid = int(sid)
        if kind == "fleet":
            if fids is not None and sid not in fids:
                return None
            base["fleet_id"] = sid
            return base
        if kind == "category":
            codes = current_user_categories()
            c = db.session.get(VehicleCategory, sid)
            if not c or (codes is not None and c.code not in codes):
                return None
            base["category_id"] = sid
            return base
        if kind == "vehicle":
            v = db.session.get(Vehicle, sid)
            if not v or (fids is not None and v.fleet_id not in fids):
                return None
            base["vehicle_id"] = sid
            return base
    return None


def _read_rule_form(rule):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    rtype = (request.form.get("type") or "").strip()
    severity = (request.form.get("severity") or "warning").strip()

    service_type = (request.form.get("service_type") or "").strip()

    if not name:
        return None, t["maint.err.name_required"]
    if rtype not in RULE_TYPES:
        return None, t["maint.err.type_required"]
    if service_type not in RECORD_TYPES:
        return None, t["maint.err.service_type_required"]
    if severity not in SEVERITIES:
        severity = "warning"

    interval, e1 = _num(request.form.get("interval"), lambda s: int(round(float(s))))
    if e1 or not interval or interval <= 0:
        return None, t["maint.err.interval_required"]
    warn, e2 = _num(request.form.get("advance_warning"), lambda s: int(round(float(s))))
    if e2:
        return None, t["maint.err.bad_number"]

    target = _parse_target(request.form.get("target") or "all")
    if target is None:
        return None, t["maint.err.target"]

    # A trips/hours rule must match the tracking of a category- or vehicle-scoped
    # target (fleet/all targets span mixed tracking — the engine filters those
    # per vehicle).
    allowed = maintenance_engine.RULE_TRACKING.get(rtype)
    if allowed:
        track = None
        if target["category_id"]:
            c = db.session.get(VehicleCategory, target["category_id"])
            track = c.tracking if c else None
        elif target["vehicle_id"]:
            v = db.session.get(Vehicle, target["vehicle_id"])
            track = v.category.tracking if v and v.category else None
        if track is not None and track not in allowed:
            return None, t["maint.err.tracking_mismatch"]

    data = dict(
        name=name, type=rtype, service_type=service_type, interval=interval,
        advance_warning=warn, severity=severity,
        is_active=request.form.get("is_active") is not None,
        **target,
    )
    return data, None


def _rule_form_ctx(rule):
    return {
        "rule_types": RULE_TYPES,
        "record_types": RECORD_TYPES,
        "severities": SEVERITIES,
        "fleets": with_current_fleet(_accessible_fleets(), rule.fleet if rule else None),
        "categories": _accessible_categories(),
        "vehicles": _accessible_vehicles(),
        "form_active": (request.form.get("is_active") is not None)
        if request.method == "POST" else (rule.is_active if rule else True),
        "target_value": (request.form.get("target")
                         or (_rule_target_value(rule) if rule else "all")),
    }


def _render_rule_form(rule, error=None):
    tpl = "_rule_form.html" if is_modal_request() else "rule_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, rule=rule, error=error, **_rule_form_ctx(rule)), status


def _get_rule_or_404(rid):
    r = db.session.get(MaintenanceRule, rid)
    if not r:
        abort(404)
    if not _rule_accessible(r):
        abort(403)
    return r


@maintenance_bp.route("/maintenance/rules")
@login_required
@require_perm("maintenance_rule.view")
def rules():
    rows = [r for r in MaintenanceRule.query.order_by(MaintenanceRule.name).all()
            if _rule_accessible(r)]
    return render_template("maintenance_rules.html", rules=rows,
                           target_label=_target_label)


@maintenance_bp.route("/maintenance/rules/new", methods=["GET", "POST"])
@login_required
@require_perm("maintenance_rule.create")
def rule_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_rule_form(None)
        if error:
            return _render_rule_form(None, error)
        r = MaintenanceRule(created_by=current_user.id, **data)
        db.session.add(r)
        db.session.flush()
        log_action("CREATE", "maintenance_rule", resource_id=r.id,
                   detail=f"Created rule '{r.name}'")
        db.session.commit()
        flash("success|" + t["maint.rule_created"])
        return modal_ok() if is_modal_request() else redirect(url_for("maintenance.rules"))
    return _render_rule_form(None)


@maintenance_bp.route("/maintenance/rules/<int:rid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("maintenance_rule.edit")
def rule_edit(rid):
    rule = _get_rule_or_404(rid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_rule_form(rule)
        if error:
            return _render_rule_form(rule, error)
        for k, v in data.items():
            setattr(rule, k, v)
        log_action("UPDATE", "maintenance_rule", resource_id=rule.id,
                   detail=f"Updated rule '{rule.name}'")
        db.session.commit()
        flash("success|" + t["maint.rule_updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("maintenance.rules"))
    return _render_rule_form(rule)


@maintenance_bp.route("/maintenance/rules/<int:rid>/delete", methods=["POST"])
@login_required
@require_perm("maintenance_rule.delete")
def rule_delete(rid):
    rule = _get_rule_or_404(rid)
    t = get_t()
    name = rule.name
    Alert.query.filter_by(rule_id=rid).delete()
    db.session.delete(rule)
    log_action("DELETE", "maintenance_rule", resource_id=rid, detail=f"Deleted rule '{name}'")
    db.session.commit()
    flash("success|" + t["maint.rule_deleted"])
    return redirect(url_for("maintenance.rules"))


def _target_label(rule):
    t = get_t()
    if rule.all_vehicles:
        return t["maint.target.all"]
    if rule.fleet_id:
        f = db.session.get(Fleet, rule.fleet_id)
        return f"{t['maint.target.fleet']}: {f.name}" if f else "—"
    if rule.category_id:
        c = db.session.get(VehicleCategory, rule.category_id)
        return f"{t['maint.target.category']}: {c.label}" if c else "—"
    if rule.vehicle_id:
        v = db.session.get(Vehicle, rule.vehicle_id)
        return f"{t['maint.target.vehicle']}: {v.code}" if v else "—"
    return "—"


# ── Records ──────────────────────────────────────────────────────────────────


def _scoped_records():
    q = MaintenanceRecord.query.join(Vehicle, MaintenanceRecord.vehicle_id == Vehicle.id)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q


def _get_record_or_404(mid):
    r = db.session.get(MaintenanceRecord, mid)
    if not r:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and r.vehicle.fleet_id not in fids:
        abort(403)
    return r


def _read_record_form(record):
    t = get_t()
    vehicle_id = request.form.get("vehicle_id", type=int)
    rtype = (request.form.get("type") or "").strip()
    date_str = (request.form.get("date") or "").strip()

    if not vehicle_id:
        return None, t["maint.err.vehicle_required"]
    vehicle = db.session.get(Vehicle, vehicle_id)
    if not vehicle:
        return None, t["maint.err.vehicle_required"]
    fids = current_user_fleet_ids()
    if fids is not None and vehicle.fleet_id not in fids:
        return None, t["error.forbidden"]
    if rtype not in RECORD_TYPES:
        return None, t["maint.err.rtype_required"]
    if not date_str or not _valid_date(date_str):
        return None, t["maint.err.date_required"]

    cost, e3 = _num(request.form.get("cost"),
                    lambda s: int(round(float(s.replace(" ", "")))))
    if e3:
        return None, t["maint.err.bad_number"]

    # A cost has to say how it was paid, since it becomes a ledger row.
    method = (request.form.get("payment_method") or "").strip() or None
    if cost is not None and method not in PAYMENT_METHODS:
        return None, t["expense.err.payment_required"]

    parts, e4 = _read_part_lines()
    if e4:
        return None, e4

    rule_id = request.form.get("rule_id", type=int) or None
    data = dict(
        vehicle_id=vehicle_id, rule_id=rule_id, type=rtype, date=date_str,
        operator=(request.form.get("operator") or "").strip() or None,
        supplier=(request.form.get("supplier") or "").strip() or None,
        description=(request.form.get("description") or "").strip() or None,
        # Not columns of MaintenanceRecord — split out by the caller and carried
        # through the approval payload so a replay rebuilds the ledger row too.
        cost=cost,
        payment_method=method,
        payment_reference=(request.form.get("payment_reference") or "").strip() or None,
        parts=parts,
    )
    return data, None


def _read_part_lines():
    """Read the "parts used" rows off the form: two parallel lists, one blank
    row being the norm rather than the exception. Quantities for the same part
    are added up, so the service ends with one movement per part.
    Returns (lines, error) where lines is [{"part_id": int, "quantity": float}].
    """
    t = get_t()
    ids = request.form.getlist("part_id")
    qtys = request.form.getlist("part_qty")
    merged = {}
    order = []
    for raw_id, raw_qty in zip(ids, qtys):
        if not (raw_id or "").strip():
            continue
        try:
            pid = int(raw_id)
        except ValueError:
            return None, t.get("mv.err.part", "Choisissez un article actif.")
        part = db.session.get(Part, pid)
        if not part:
            return None, t.get("mv.err.part", "Choisissez un article actif.")
        try:
            qty = float((raw_qty or "").replace(",", "."))
        except ValueError:
            return None, t.get("mv.err.quantity", "Quantité invalide.")
        if qty <= 0:
            return None, t.get("mv.err.quantity", "Quantité invalide.")
        if pid not in merged:
            order.append(pid)
        merged[pid] = merged.get(pid, 0.0) + qty
    return [{"part_id": pid, "quantity": merged[pid]} for pid in order], None


# Keys in the form payload that belong to the ledger row, not the service record.
MONEY_KEYS = ("cost", "payment_method", "payment_reference")
# … and the parts used, which belong to the store.
PARTS_KEY = "parts"


def split_money(data):
    """Pop the ledger fields out of a record payload. Returns (data, money)."""
    return data, {k: data.pop(k, None) for k in MONEY_KEYS}


def split_parts(data):
    """Pop the parts lines out of a record payload. Returns (data, lines)."""
    return data, data.pop(PARTS_KEY, None) or []


def sync_service_expense(record, money):
    """Mirror a service's cost into the money ledger as its single 'entretien'
    row: created, updated, or removed so the two can never disagree."""
    cost = money.get("cost")
    existing = record.expense
    if cost is None:
        if existing:
            db.session.delete(existing)
        return
    fields = dict(
        vehicle_id=record.vehicle_id,
        fleet_id=record.vehicle.fleet_id if record.vehicle else None,
        category=MAINTENANCE_CATEGORY,
        date=record.date,
        amount=cost,
        currency="GNF",
        payment_method=money.get("payment_method"),
        payment_reference=money.get("payment_reference"),
        operator=record.operator,
        supplier=record.supplier,
        description=record.description,
    )
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
    else:
        db.session.add(Expense(maintenance_record_id=record.id,
                               created_by=getattr(current_user, "id", None), **fields))


def sync_record_parts(record, lines):
    """Mirror the parts used into the store as this service's sortie movements.

    A line that is already there keeps the value frozen when it left the shelf —
    only its quantity moves — so editing a service never re-prices what it
    already consumed. A new line is valued at the weighted average as of the
    service date. A line that disappeared is deleted, which puts the parts back
    on the shelf and drops that much off the vehicle's cost.

    These movements carry no money of their own: the parts were paid for when
    they were received. They only say which machine the cost was for.
    """
    existing = {m.part_id: m for m in record.part_movements if m.kind == "sortie"}
    for line in lines:
        pid = line["part_id"]
        mv = existing.pop(pid, None)
        if mv:
            mv.quantity = line["quantity"]
            mv.date = record.date
            continue
        part = db.session.get(Part, pid)
        if not part:
            continue
        db.session.add(StockMovement(
            part_id=pid, kind="sortie", date=record.date,
            quantity=line["quantity"],
            unit_value=part.average_cost_as_of(record.date),
            maintenance_record_id=record.id,
            created_by=getattr(current_user, "id", None)))
    for mv in existing.values():
        db.session.delete(mv)


def short_parts(record, lines):
    """Parts whose shelf would go under zero once these lines are applied.
    Warned about, never blocked: the part was fitted this morning and the
    receipt may well be logged this afternoon.

    Returns [(part, remaining)] — remaining is negative.
    """
    already = {m.part_id: m.quantity
               for m in record.part_movements if m.kind == "sortie"} if record else {}
    out = []
    for line in lines:
        part = db.session.get(Part, line["part_id"])
        if not part:
            continue
        # Add back what this service already took, so an edit compares against
        # the shelf as it stands without it.
        available = part.quantity + already.get(part.id, 0.0)
        remaining = available - line["quantity"]
        if remaining < 0:
            out.append((part, remaining))
    return out


def _flash_short_parts(short):
    """Tell the user the shelf went under zero, without standing in their way.
    Worded as a stock figure to put right, not as a reproach."""
    if not short:
        return
    t = get_t()
    names = ", ".join(f"{p.name} ({rem:,.10g})" for p, rem in short)
    flash("warning|" + t.get(
        "maint.parts_short",
        "Stock insuffisant, la saisie est conservée : %(names)s. "
        "Enregistrez la réception pour remettre le compte à jour.")
        % {"names": names})


def _record_form_ctx(record):
    preset_rule = request.args.get("rule_id", type=int)
    preset_type = None
    if preset_rule:
        r = db.session.get(MaintenanceRule, preset_rule)
        if r and r.service_type:
            preset_type = r.service_type
    # Re-render the parts rows the user submitted, so a rejected form doesn't
    # lose them; otherwise the ones already on the record.
    if request.method == "POST":
        submitted = list(zip(request.form.getlist("part_id"),
                             request.form.getlist("part_qty")))
        part_rows = [(pid, qty) for pid, qty in submitted if (pid or "").strip()]
    elif record:
        part_rows = [(str(m.part_id), f"{m.quantity:.10g}")
                     for m in record.part_movements if m.kind == "sortie"]
    else:
        part_rows = []

    return {
        "record_types": RECORD_TYPES,
        "vehicles": _accessible_vehicles(),
        "operators": _accessible_operators(),
        "payment_methods": PAYMENT_METHODS,
        "stock_parts": Part.query.filter(Part.is_active.is_(True))
                                 .order_by(Part.name).all(),
        "part_rows": part_rows,
        "preset_vehicle": request.args.get("vehicle_id", type=int),
        "preset_rule": preset_rule,
        "preset_type": preset_type,
        "today": datetime.utcnow().date().isoformat(),
    }


def _render_record_form(record, error=None):
    tpl = "_record_form.html" if is_modal_request() else "record_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, record=record, error=error, **_record_form_ctx(record)), status


@maintenance_bp.route("/maintenance/records")
@login_required
@require_perm("maintenance_record.view")
def records():
    fv = request.args.get("vehicle_id", type=int)
    q = _scoped_records()
    if fv:
        q = q.filter(MaintenanceRecord.vehicle_id == fv)
    rows = q.order_by(MaintenanceRecord.date.desc(), MaintenanceRecord.id.desc()).limit(300).all()
    return render_template("maintenance_records.html", records=rows,
                           vehicles=_accessible_vehicles(), fv=fv)


def _close_alert_for_record(record):
    """When a service is logged against a rule, resolve that rule's open alert."""
    if not record.rule_id:
        return
    al = (Alert.query.filter_by(rule_id=record.rule_id, vehicle_id=record.vehicle_id)
          .filter(Alert.status.in_(("open", "snoozed")))
          .order_by(Alert.id.desc()).first())
    if al:
        al.status = "resolved"
        al.resolved_at = datetime.utcnow()
        al.resolved_by = current_user.id
        al.resolution_maintenance_id = record.id
        al.resolution_note = "Entretien enregistré"


@maintenance_bp.route("/maintenance/records/new", methods=["GET", "POST"])
@login_required
@require_perm("maintenance_record.create")
def record_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_record_form(None)
        if error:
            return _render_record_form(None, error)
        vehicle = db.session.get(Vehicle, data["vehicle_id"])
        if needs_approval("maintenance_record.create"):
            submit_change(resource_type="maintenance_record", action="create",
                          fleet_id=vehicle.fleet_id, payload=data)
            flash("success|" + t["maint.record_submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("maintenance.records"))
        data, money = split_money(data)
        data, lines = split_parts(data)
        short = short_parts(None, lines)
        rec = MaintenanceRecord(recorded_by=current_user.id, **data)
        db.session.add(rec)
        db.session.flush()
        sync_service_expense(rec, money)
        sync_record_parts(rec, lines)
        _close_alert_for_record(rec)
        maintenance_engine.evaluate_vehicle(vehicle)
        log_action("CREATE", "maintenance_record", resource_id=rec.id,
                   fleet_id=vehicle.fleet_id, detail=f"Logged {rec.type} on {vehicle.code}")
        db.session.commit()
        flash("success|" + t["maint.record_created"])
        _flash_short_parts(short)
        return modal_ok() if is_modal_request() else redirect(url_for("maintenance.records"))
    return _render_record_form(None)


@maintenance_bp.route("/maintenance/records/<int:mid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("maintenance_record.edit")
def record_edit(mid):
    record = _get_record_or_404(mid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_record_form(record)
        if error:
            return _render_record_form(record, error)
        vehicle = db.session.get(Vehicle, data["vehicle_id"])
        if needs_approval("maintenance_record.edit", record.recorded_by, record.recorded_at):
            submit_change(resource_type="maintenance_record", action="update",
                          resource_id=record.id, fleet_id=vehicle.fleet_id, payload=data)
            flash("success|" + t["maint.record_submitted"])
            return modal_ok() if is_modal_request() else redirect(url_for("maintenance.records"))
        data, money = split_money(data)
        data, lines = split_parts(data)
        short = short_parts(record, lines)
        for k, v in data.items():
            setattr(record, k, v)
        db.session.flush()
        sync_service_expense(record, money)
        sync_record_parts(record, lines)
        maintenance_engine.evaluate_vehicle(vehicle)
        log_action("UPDATE", "maintenance_record", resource_id=record.id,
                   fleet_id=vehicle.fleet_id, detail=f"Edited record #{record.id}")
        db.session.commit()
        flash("success|" + t["maint.record_updated"])
        _flash_short_parts(short)
        return modal_ok() if is_modal_request() else redirect(url_for("maintenance.records"))
    return _render_record_form(record)


@maintenance_bp.route("/maintenance/records/<int:mid>/delete", methods=["POST"])
@login_required
@require_perm("maintenance_record.delete")
def record_delete(mid):
    record = _get_record_or_404(mid)
    t = get_t()
    vid, fleet_id = record.vehicle_id, record.vehicle.fleet_id
    db.session.delete(record)
    db.session.flush()
    maintenance_engine.evaluate_vehicle(db.session.get(Vehicle, vid))
    log_action("DELETE", "maintenance_record", resource_id=mid, fleet_id=fleet_id,
               detail=f"Deleted record #{mid}")
    db.session.commit()
    flash("success|" + t["maint.record_deleted"])
    return redirect(request.referrer or url_for("maintenance.records"))


# ── Alerts ───────────────────────────────────────────────────────────────────


def _scoped_alerts():
    q = Alert.query.join(Vehicle, Alert.vehicle_id == Vehicle.id)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q


def _get_alert_or_404(aid):
    a = db.session.get(Alert, aid)
    if not a:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and a.vehicle.fleet_id not in fids:
        abort(403)
    return a


@maintenance_bp.route("/alerts")
@login_required
@require_perm("alert.view")
def alerts():
    # Lazy re-evaluation (stand-in for the nightly cron).
    maintenance_engine.evaluate_vehicles(_accessible_vehicles())
    db.session.commit()

    show = request.args.get("status") or "active"
    q = _scoped_alerts()
    if show == "active":
        q = q.filter(Alert.status == "open")
    elif show in ("snoozed", "resolved", "dismissed"):
        q = q.filter(Alert.status == show)
    alerts_rows = q.order_by(Alert.triggered_at.desc()).limit(300).all()
    active_count = _scoped_alerts().filter(Alert.status == "open").count()
    snoozed_count = _scoped_alerts().filter(Alert.status == "snoozed").count()
    return render_template("alerts.html", alerts=alerts_rows, show=show,
                           active_count=active_count, snoozed_count=snoozed_count)


@maintenance_bp.route("/alerts/<int:aid>/snooze", methods=["POST"])
@login_required
@require_perm("alert.resolve")
def alert_snooze(aid):
    a = _get_alert_or_404(aid)
    a.status = "snoozed"
    a.snoozed_until = datetime.utcnow() + timedelta(days=SNOOZE_DAYS)
    log_action("UPDATE", "alert", resource_id=aid, detail="Snoozed alert")
    db.session.commit()
    flash("success|" + get_t()["maint.alert_snoozed"])
    return redirect(url_for("maintenance.alerts"))


# An alert is only "resolved" by logging the actual maintenance (which resets
# the rule counter); there is no manual resolve. Snooze / dismiss handle the
# rest. The engine also auto-resolves an open alert once it's no longer due.


@maintenance_bp.route("/alerts/<int:aid>/dismiss", methods=["POST"])
@login_required
@require_perm("alert.dismiss")
def alert_dismiss(aid):
    a = _get_alert_or_404(aid)
    a.status = "dismissed"
    log_action("UPDATE", "alert", resource_id=aid, detail="Dismissed alert")
    db.session.commit()
    flash("success|" + get_t()["maint.alert_dismissed"])
    return redirect(url_for("maintenance.alerts"))
