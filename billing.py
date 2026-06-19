"""Billing helpers — dated per-client (fleet) rates and invoice math.

A fleet models a client; its FleetRate rows give the GNF-per-worked-unit price
for each machine type, with dated history. Billing is per worked unit: a daily
entry's billable amount = units (trips, or hours incl. index-derived) × the
rate in force on that entry's date.
"""
from datetime import date

from models import FleetRate


def rate_on(fleet_id, category_code, on_date):
    """Rate (GNF/unit) in force for (fleet, category) on `on_date` (YYYY-MM-DD
    string), or None if no rate was set yet on/before that date."""
    row = (FleetRate.query
           .filter_by(fleet_id=fleet_id, category_code=category_code)
           .filter(FleetRate.effective_from <= on_date)
           .order_by(FleetRate.effective_from.desc(), FleetRate.id.desc())
           .first())
    return row.rate_per_unit if row else None


def current_rates(fleet_id, on_date=None):
    """{category_code: rate} in force for a fleet on `on_date` (default today).

    Walks the history ascending so later effective rows overwrite earlier ones,
    leaving the most recent rate per category.
    """
    on_date = on_date or date.today().isoformat()
    out = {}
    rows = (FleetRate.query.filter_by(fleet_id=fleet_id)
            .filter(FleetRate.effective_from <= on_date)
            .order_by(FleetRate.effective_from.asc(), FleetRate.id.asc())
            .all())
    for r in rows:
        out[r.category_code] = r.rate_per_unit
    return out


def rate_history(fleet_id):
    """{category_code: [(effective_from, rate), ...]} ascending — preloaded so a
    whole month can be priced without a query per entry."""
    book = {}
    rows = (FleetRate.query.filter_by(fleet_id=fleet_id)
            .order_by(FleetRate.effective_from.asc(), FleetRate.id.asc()).all())
    for r in rows:
        book.setdefault(r.category_code, []).append((r.effective_from, r.rate_per_unit))
    return book


def resolve(book, category_code, on_date):
    """Latest rate in `book` for category_code with effective_from <= on_date."""
    best = None
    for eff, rate in book.get(category_code, []):
        if eff <= on_date:
            best = rate
        else:
            break
    return best


def entry_units(entry, unit_type):
    """Worked units billed for a daily entry, given its category's unit_type.

    trips → the trip count; hours → worked hours (already index-derived in the
    entry's `hours` column). Returns 0.0 when the field is blank.
    """
    if unit_type == "trips":
        return float(entry.trips or 0)
    return float(entry.hours or 0)
