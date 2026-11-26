"""Billing helpers — dated tarifs and the month's math.

The price of a machine's work lives on a tarif (BillingTag): a named price
that machines share. The machine carries the tag, dated (VehicleTag), and
the tag carries its price, dated (BillingTagRate) — both append-only, so a
past month re-bills exactly as it was: the tag the machine carried that day,
at the price that tag had that day. Changing either inserts a new row and
never moves what came before.

A daily entry's billable amount = its units × that rate on its date.

Machines priced before tarifs existed keep their per-machine ClientRate rows
as a fallback: a day with no tag price falls back to them, so nothing ever
re-bills differently. The earlier per-fleet-and-category rates (FleetRate)
are kept in the database untouched but no longer read.
"""
import calendar

from models import BillingTag, BillingTagRate, ClientRate, SupplierRate, VehicleTag


def latest(pairs, on_date):
    """The last value in ascending (effective_from, value) pairs whose date
    is on or before `on_date`, or None."""
    best = None
    for eff, val in pairs:
        if eff <= on_date:
            best = val
        else:
            break
    return best


def tag_book():
    """{vehicle_id: [(effective_from, tag_id), ...]} ascending — every
    machine's tarif history, small enough to load whole."""
    book = {}
    rows = (VehicleTag.query
            .order_by(VehicleTag.effective_from.asc(), VehicleTag.id.asc()).all())
    for r in rows:
        book.setdefault(r.vehicle_id, []).append((r.effective_from, r.tag_id))
    return book


def price_book():
    """{tag_id: [(effective_from, rate), ...]} ascending — every tarif's
    price history."""
    book = {}
    rows = (BillingTagRate.query
            .order_by(BillingTagRate.effective_from.asc(), BillingTagRate.id.asc()).all())
    for r in rows:
        book.setdefault(r.tag_id, []).append((r.effective_from, r.rate_per_unit))
    return book


def rate_book(client_id):
    """Everything needed to price a client's month without a query per entry:
    the machines' tarif history, the tarifs' price history, what each tarif
    counts (per unit or per month, GNF or USD), and the client's old
    per-machine prices as a fallback."""
    legacy = {}
    rows = (ClientRate.query.filter_by(client_id=client_id)
            .order_by(ClientRate.effective_from.asc(), ClientRate.id.asc()).all())
    for r in rows:
        legacy.setdefault(r.vehicle_id, []).append((r.effective_from, r.rate_per_unit))
    kinds = {tg.id: (tg.basis, tg.currency) for tg in BillingTag.query.all()}
    return {"tags": tag_book(), "prices": price_book(), "legacy": legacy,
            "kinds": kinds}


def tag_on(book, vehicle_id, on_date):
    """The tarif (tag id) the machine carried on `on_date`, or None."""
    return latest(book["tags"].get(vehicle_id, []), on_date)


def price_on(book, vehicle_id, on_date):
    """What the machine bills at on a date: (basis, currency, rate).

    Its tarif that day gives the basis — "unit" (per trip / hour) or
    "month" (a monthly price, divided by the days of that month) — the
    currency, and the price that tag had that day. A machine without a
    tarif, or whose tarif had no price yet that early, falls back to its
    old per-machine prices, always per unit in GNF. (None, None, None)
    when nothing prices the day."""
    tag_id = tag_on(book, vehicle_id, on_date)
    if tag_id is not None:
        rate = latest(book["prices"].get(tag_id, []), on_date)
        if rate is not None:
            basis, cur = book["kinds"].get(tag_id, ("unit", "GNF"))
            return basis, cur, rate
    rate = latest(book["legacy"].get(vehicle_id, []), on_date)
    if rate is not None:
        return "unit", "GNF", rate
    return None, None, None


def rate_on(book, vehicle_id, on_date):
    """The bare price on a date, whatever its basis — for display."""
    return price_on(book, vehicle_id, on_date)[2]


def days_in_month(on_date):
    """How many days the date's month really has: 28, 29, 30 or 31."""
    y, m = int(on_date[:4]), int(on_date[5:7])
    return calendar.monthrange(y, m)[1]


def day_weight(entry):
    """What a saisie is worth in days: a half day marked in it counts 0.5,
    anything else 1."""
    return 0.5 if entry.half_day else 1.0


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
