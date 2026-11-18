"""Billing helpers — dated per-machine client rates and the month's math.

A client has machines placed with it, each with its own price: GNF per worked
unit (trip or hour, whichever its category counts). Prices are dated,
append-only history in ClientRate: changing one inserts a new row, so a past
month re-bills at the price that applied then. The rate in force on a date is
the row with the greatest effective_from on or before it.

A daily entry's billable amount = its units × that rate on its date.

The earlier per-fleet-and-category rates (FleetRate) are kept in the database
untouched but no longer read: the price belongs to the machine and its client.
"""
from models import ClientRate, SupplierRate


def rate_book(client_id):
    """{vehicle_id: [(effective_from, rate), ...]} ascending -- preloaded so a
    whole month can be priced without a query per entry."""
    book = {}
    rows = (ClientRate.query.filter_by(client_id=client_id)
            .order_by(ClientRate.effective_from.asc(), ClientRate.id.asc()).all())
    for r in rows:
        book.setdefault(r.vehicle_id, []).append((r.effective_from, r.rate_per_unit))
    return book


def rate_on(book, vehicle_id, on_date):
    """Latest rate in `book` for the machine with effective_from <= on_date."""
    best = None
    for eff, rate in book.get(vehicle_id, []):
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


# ── What a lessor is paid ────────────────────────────────────────────────────
# The cost side, for the day profit per machine is worked out: SupplierRate
# holds, per machine, GNF per worked unit or per month, dated the same way.

def lease_book(supplier_id):
    """{vehicle_id: [(effective_from, basis, rate), ...]} ascending."""
    book = {}
    rows = (SupplierRate.query.filter_by(supplier_id=supplier_id)
            .order_by(SupplierRate.effective_from.asc(), SupplierRate.id.asc()).all())
    for r in rows:
        book.setdefault(r.vehicle_id, []).append((r.effective_from, r.basis, r.rate))
    return book


def lease_on(book, vehicle_id, on_date):
    """(basis, rate) in force for the machine on `on_date`, or None."""
    best = None
    for eff, basis, rate in book.get(vehicle_id, []):
        if eff <= on_date:
            best = (basis, rate)
        else:
            break
    return best
