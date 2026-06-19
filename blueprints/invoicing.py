"""Facturation (invoicing) blueprint.

Per client (fleet) × month: bill each machine by its worked units (trips, or
hours incl. index-derived) × the rate in force on each entry's date. Rates are
the dated FleetRate history, so a mid-month price change is applied correctly.
"""
from datetime import datetime

from flask import Blueprint, render_template, request
from flask_login import login_required

import billing
from app import current_user_fleet_ids, require_perm
from models import DailyEntry, Fleet, Vehicle, VehicleCategory

invoicing_bp = Blueprint("invoicing", __name__)


def _accessible_fleets():
    fids = current_user_fleet_ids()
    q = Fleet.query.order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _valid_month(s):
    try:
        datetime.strptime(s, "%Y-%m")
        return True
    except (TypeError, ValueError):
        return False


@invoicing_bp.route("/facturation")
@login_required
@require_perm("report.view")
def index():
    now = datetime.utcnow()
    month = request.args.get("month", "")
    if not _valid_month(month):
        month = now.strftime("%Y-%m")

    fleets = _accessible_fleets()
    fleet_id = request.args.get("fleet_id", type=int)
    fleet = None
    if fleet_id:
        fleet = next((f for f in fleets if f.id == fleet_id), None)
    elif fleets:
        fleet = fleets[0]

    rows, total, any_missing = [], 0, False
    if fleet:
        cats = {c.code: c for c in VehicleCategory.query.all()}
        book = billing.rate_history(fleet.id)
        like = month + "%"
        month_end = month + "-31"  # for the "rate in force" reference column
        vehicles = (Vehicle.query.filter_by(fleet_id=fleet.id)
                    .order_by(Vehicle.code).all())
        for v in vehicles:
            unit_type = v.category.unit_type
            entries = (DailyEntry.query.filter_by(vehicle_id=v.id)
                       .filter(DailyEntry.date.like(like)).all())
            units = 0.0
            amount = 0.0
            missing = False
            for e in entries:
                u = billing.entry_units(e, unit_type)
                if u <= 0:
                    continue
                units += u
                rate = billing.resolve(book, v.category.code, e.date)
                if rate is None:
                    missing = True
                else:
                    amount += u * rate
            if units > 0:
                rows.append({
                    "v": v, "unit_type": unit_type, "units": units,
                    "amount": int(round(amount)), "missing": missing,
                    "ref_rate": billing.resolve(book, v.category.code, month_end),
                })
                total += int(round(amount))
                any_missing = any_missing or missing
        rows.sort(key=lambda r: r["amount"], reverse=True)

    return render_template(
        "invoicing.html",
        fleets=fleets, fleet=fleet, month=month,
        rows=rows, total=total, any_missing=any_missing,
    )
