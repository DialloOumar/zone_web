"""Maintenance rule engine.

Evaluates recurring maintenance rules (km / hours / time) against a vehicle's
running totals and its maintenance history, opening / refreshing / resolving
Alerts as the situation changes. Standalone (imports only models) so both the
entries blueprint (real-time on entry save) and the maintenance blueprint
(lazy, on the alerts page) can call it.

Deferred for now: anomaly-type rules, email/WhatsApp delivery, a nightly cron
(time rules are re-evaluated whenever the alert centre is opened). Messages are
generated in French (the site default); callers commit the session.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_

from models import Alert, DailyEntry, MaintenanceRecord, MaintenanceRule, db

RECURRING_TYPES = ("km_recurring", "hours_recurring", "time_recurring")


def _service_filter(vehicle, rule):
    """Records that count as 'this rule was serviced': either explicitly linked
    to the rule, or a service of the rule's service type on the same vehicle."""
    conds = [MaintenanceRecord.rule_id == rule.id]
    if rule.service_type:
        conds.append(MaintenanceRecord.type == rule.service_type)
    return (MaintenanceRecord.vehicle_id == vehicle.id), or_(*conds)


def _targets(rule, vehicle):
    if rule.all_vehicles:
        return True
    if rule.vehicle_id:
        return rule.vehicle_id == vehicle.id
    if rule.category_id:
        return rule.category_id == vehicle.category_id
    if rule.fleet_id:
        return rule.fleet_id == vehicle.fleet_id
    return False


def _current(vehicle, column):
    return db.session.query(func.max(column)).filter(
        DailyEntry.vehicle_id == vehicle.id).scalar() or 0


def _last_service_metric(vehicle, rule, attr):
    col = getattr(MaintenanceRecord, attr)
    return db.session.query(func.max(col)).filter(*_service_filter(vehicle, rule)).scalar() or 0


def _last_service_date(vehicle, rule):
    rec = (MaintenanceRecord.query.filter(*_service_filter(vehicle, rule))
           .order_by(MaintenanceRecord.date.desc()).first())
    return rec.date if rec else None


def _serviced_since(vehicle, rule, ts):
    if ts is None:
        return False
    return db.session.query(MaintenanceRecord.id).filter(
        *_service_filter(vehicle, rule),
        MaintenanceRecord.recorded_at > ts).first() is not None


def _assess(rule, vehicle, now):
    """Return (active, overdue, message) or None if the rule can't be assessed."""
    if not rule.interval:
        return None
    warn = rule.advance_warning or 0

    if rule.type == "km_recurring":
        current = _current(vehicle, DailyEntry.cumulative_km)
        remaining = (_last_service_metric(vehicle, rule, "kilometers_at")
                     + rule.interval) - current
        overdue = remaining <= 0
        soon = 0 < remaining <= warn
        if not (overdue or soon):
            return (False, False, "")
        msg = (f"{rule.name} : en retard de {abs(remaining):.0f} km" if overdue
               else f"{rule.name} : échéance dans {remaining:.0f} km")
        return (True, overdue, msg)

    if rule.type == "hours_recurring":
        current = _current(vehicle, DailyEntry.cumulative_hours)
        remaining = (_last_service_metric(vehicle, rule, "hours_at")
                     + rule.interval) - current
        overdue = remaining <= 0
        soon = 0 < remaining <= warn
        if not (overdue or soon):
            return (False, False, "")
        msg = (f"{rule.name} : en retard de {abs(remaining):.0f} h" if overdue
               else f"{rule.name} : échéance dans {remaining:.0f} h")
        return (True, overdue, msg)

    if rule.type == "time_recurring":
        base = _last_service_date(vehicle, rule)
        start = (date.fromisoformat(base) if base
                 else vehicle.created_at.date())
        remaining = ((start + timedelta(days=rule.interval)) - now.date()).days
        overdue = remaining <= 0
        soon = 0 < remaining <= warn
        if not (overdue or soon):
            return (False, False, "")
        msg = (f"{rule.name} : en retard de {abs(remaining)} jours" if overdue
               else f"{rule.name} : échéance dans {remaining} jours")
        return (True, overdue, msg)

    return None  # anomaly / unknown — not evaluated yet


def _apply(rule, vehicle, assessed, now):
    active, overdue, msg = assessed
    last = (Alert.query.filter_by(rule_id=rule.id, vehicle_id=vehicle.id)
            .order_by(Alert.id.desc()).first())

    if active:
        severity = rule.severity if not overdue else (
            "critical" if rule.severity == "critical" else "warning")
        if last and last.status in ("open", "snoozed"):
            if last.status == "snoozed" and last.snoozed_until and last.snoozed_until <= now:
                last.status = "open"
            last.message = msg
            last.severity = severity
        elif last and last.status == "dismissed" and not _serviced_since(vehicle, rule, last.triggered_at):
            return  # respect the dismissal until the vehicle is serviced
        else:
            db.session.add(Alert(rule_id=rule.id, vehicle_id=vehicle.id,
                                 triggered_at=now, severity=severity,
                                 message=msg, status="open"))
    else:
        if last and last.status in ("open", "snoozed"):
            last.status = "resolved"
            last.resolved_at = now
            last.resolution_note = "Auto : plus dû"


def evaluate_vehicle(vehicle, now=None):
    """Evaluate every active recurring rule that targets this vehicle."""
    if not vehicle or not vehicle.is_active:
        return
    now = now or datetime.utcnow()
    rules = MaintenanceRule.query.filter(
        MaintenanceRule.is_active.is_(True),
        MaintenanceRule.type.in_(RECURRING_TYPES)).all()
    for rule in rules:
        if not _targets(rule, vehicle):
            continue
        assessed = _assess(rule, vehicle, now)
        if assessed is not None:
            _apply(rule, vehicle, assessed, now)


def evaluate_vehicles(vehicles, now=None):
    now = now or datetime.utcnow()
    for v in vehicles:
        evaluate_vehicle(v, now)
