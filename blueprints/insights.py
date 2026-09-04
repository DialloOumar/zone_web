"""Operations insights blueprint — owner-facing analytics over the fleet.

Three lenses, all fleet-scoped to the current user and filtered by month:
  1. Fuel consumption vs. baseline  — actual liters vs. expected (units ×
     the vehicle/category baseline). The gap is where fuel loss, theft or a
     failing engine hides.
  2. Cost per vehicle               — total operating cost ranked, split into
     fuel / maintenance / other.
  3. Idle / under-utilised vehicles — days since last activity + units logged.

Litres come from the fuel movements; costs come from the ledger (Expense):
"entretien", written from a service record, and everything else. Fuel is not in
the ledger at all — it is followed in litres — so the fuel cost column is always
zero for now.
"""
from datetime import datetime

from flask import Blueprint, render_template, request
from flask_login import login_required

from app import current_user_fleet_ids, require_perm
from models import DailyEntry, Expense, Fleet, FuelMovement, Vehicle, db

insights_bp = Blueprint("insights", __name__)

IDLE_DAYS = 7          # no activity for this many days → flagged idle
OVER_WARN = 10.0       # fuel variance % over baseline → warning
OVER_DANGER = 25.0     # … → danger

# Financial lens (cost per vehicle, fleet total in currency) — kept but HIDDEN
# for now. It's the seed for the future paid "profitabilité / analyse financière"
# module; flip to True (or gate per plan) to surface it again.
PREMIUM_FINANCE = False


def _accessible_vehicles():
    q = Vehicle.query
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


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


def _aggregate(vehicle_ids, month):
    """Per-vehicle aggregates for the given month (YYYY-MM), keyed by id."""
    like = month + "%"
    co = db.func.coalesce

    act = {}
    for vid, trips, hours, km in (
        db.session.query(
            DailyEntry.vehicle_id,
            co(db.func.sum(DailyEntry.trips), 0),
            co(db.func.sum(DailyEntry.hours), 0.0),
            co(db.func.sum(DailyEntry.kilometers), 0.0))
        .filter(DailyEntry.vehicle_id.in_(vehicle_ids))
        .filter(DailyEntry.date.like(like))
        .group_by(DailyEntry.vehicle_id).all()
    ):
        act[vid] = {"trips": int(trips or 0), "hours": float(hours or 0), "km": float(km or 0)}

    # Litres each machine actually took, from the fuel movements: drawn from a
    # citerne, or filled straight at the pump. Fuel carries no money any more —
    # it is followed in litres — so there is no amount to read alongside.
    fuel = {}
    for vid, liters in (
        db.session.query(FuelMovement.vehicle_id,
                         co(db.func.sum(FuelMovement.liters), 0.0))
        .filter(FuelMovement.vehicle_id.in_(vehicle_ids))
        .filter(FuelMovement.kind.in_(("distribution", "direct")))
        .filter(FuelMovement.date.like(like))
        .group_by(FuelMovement.vehicle_id).all()
    ):
        fuel[vid] = {"liters": float(liters or 0), "amount": 0}

    # Everything that is neither fuel nor a service — services are counted below.
    other = {}
    for vid, amount in (
        db.session.query(Expense.vehicle_id, co(db.func.sum(Expense.amount), 0))
        .filter(Expense.vehicle_id.in_(vehicle_ids))
        .filter(Expense.category.notin_(["fuel", "entretien"]))
        .filter(Expense.date.like(like))
        .group_by(Expense.vehicle_id).all()
    ):
        other[vid] = int(amount or 0)

    # Service costs now live in the ledger too, as "entretien" rows.
    maint = {}
    for vid, amount in (
        db.session.query(Expense.vehicle_id, co(db.func.sum(Expense.amount), 0))
        .filter(Expense.vehicle_id.in_(vehicle_ids))
        .filter(Expense.category == "entretien")
        .filter(Expense.date.like(like))
        .group_by(Expense.vehicle_id).all()
    ):
        maint[vid] = int(amount or 0)

    # All-time last activity (idle detection is "as of today", not period-bound).
    last = dict(
        db.session.query(DailyEntry.vehicle_id, db.func.max(DailyEntry.date))
        .filter(DailyEntry.vehicle_id.in_(vehicle_ids))
        .group_by(DailyEntry.vehicle_id).all()
    )
    return act, fuel, other, maint, last


@insights_bp.route("/insights")
@login_required
@require_perm("insights.view")
def index():
    now = datetime.utcnow()
    today = now.date()

    month = request.args.get("month", "")
    if not _valid_month(month):
        month = now.strftime("%Y-%m")
    fleet_id = request.args.get("fleet_id", type=int)

    fleets = _accessible_fleets()
    vehicles = _accessible_vehicles()
    if fleet_id:
        vehicles = [v for v in vehicles if v.fleet_id == fleet_id]
    vids = [v.id for v in vehicles]

    act, fuel, other, maint, last = (
        _aggregate(vids, month) if vids else ({}, {}, {}, {}, {})
    )

    fuel_rows, cost_rows, idle_rows = [], [], []
    fleet_total = 0

    for v in vehicles:
        a = act.get(v.id, {})
        unit_type = v.category.unit_type
        units = a.get("trips", 0) if unit_type == "trips" else a.get("hours", 0.0)
        km = a.get("km", 0.0)

        f = fuel.get(v.id, {})
        actual_l = f.get("liters", 0.0)
        baseline = v.effective_baseline  # L per unit, or None

        # 1. Fuel vs. baseline — only when all three are present & comparable.
        if baseline and units and actual_l:
            expected_l = units * baseline
            var_l = actual_l - expected_l
            var_pct = (var_l / expected_l * 100.0) if expected_l else 0.0
            fuel_rows.append({
                "v": v, "units": units, "unit_type": unit_type,
                "actual_l": actual_l, "expected_l": expected_l,
                "var_l": var_l, "var_pct": var_pct,
            })

        # 2. Cost per vehicle.
        fuel_cost = f.get("amount", 0)
        other_cost = other.get(v.id, 0)
        maint_cost = maint.get(v.id, 0)
        total = fuel_cost + other_cost + maint_cost
        fleet_total += total
        if total or units or km:
            cost_rows.append({
                "v": v, "fuel": fuel_cost, "maint": maint_cost, "other": other_cost,
                "total": total, "units": units, "unit_type": unit_type, "km": km,
                "per_unit": (total / units) if units else None,
                "per_km": (total / km) if km else None,
            })

        # 3. Idle / under-utilised — active vehicles only.
        if v.is_active:
            ld = last.get(v.id)
            days = (today - datetime.strptime(ld, "%Y-%m-%d").date()).days if ld else None
            idle_rows.append({
                "v": v, "last_date": ld, "days": days,
                "units": units, "unit_type": unit_type,
                "idle": days is None or days >= IDLE_DAYS,
            })

    fuel_rows.sort(key=lambda r: r["var_pct"], reverse=True)
    cost_rows.sort(key=lambda r: r["total"], reverse=True)
    # Never-logged first (treated as most idle), then most days idle, then fewest units.
    idle_rows.sort(key=lambda r: (-(r["days"] if r["days"] is not None else 10 ** 9), r["units"]))

    over_count = sum(1 for r in fuel_rows if r["var_pct"] >= OVER_WARN)
    idle_count = sum(1 for r in idle_rows if r["idle"])

    # Chart blobs (top N for readability).
    fuel_chart = {
        "labels": [r["v"].code for r in fuel_rows[:12]],
        "values": [round(r["var_pct"], 1) for r in fuel_rows[:12]],
    }
    top_cost = cost_rows[:10]
    cost_chart = {
        "labels": [r["v"].code for r in top_cost],
        "fuel": [r["fuel"] for r in top_cost],
        "maint": [r["maint"] for r in top_cost],
        "other": [r["other"] for r in top_cost],
    }

    return render_template(
        "insights.html",
        month=month, fleets=fleets, fleet_id=fleet_id,
        fuel_rows=fuel_rows, cost_rows=cost_rows, idle_rows=idle_rows,
        fleet_total=fleet_total, over_count=over_count, idle_count=idle_count,
        idle_days=IDLE_DAYS, over_warn=OVER_WARN, over_danger=OVER_DANGER,
        fuel_chart=fuel_chart, cost_chart=cost_chart,
        finance_enabled=PREMIUM_FINANCE,
    )
