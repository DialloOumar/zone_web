"""Carburant blueprint — fuel tankers (citernes) and their daily movements.

First slice of the fuel rework:
  • create / activate / archive citernes (their own reservoir with a capacity);
  • log a distribution — an engin draws fuel from a citerne on a given day.

Stock is computed from FuelMovement (never stored), so a distribution simply
appends a movement and the tank level follows. Rentrées / conso / relevés reuse
the same movement table in a later step.

Helpers (require_perm, current_user_fleet_ids, log_action, get_t, modal helpers)
come from app.py; this module is imported at the bottom of app.py.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, require_perm, with_current_fleet)
from models import Citerne, Fleet, FuelMovement, Operator, Vehicle, db

carburant_bp = Blueprint("carburant", __name__, url_prefix="/carburant")

# Tolerance (litres) beyond which a gauge écart is flagged as an anomaly.
# A flat value for now; can become a per-citerne / setting later.
SEUIL_ECART = 100
PER_PAGE = 50        # rows on one screen of the ledger


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _clean_time(s):
    """Normalise an optional HH:MM time. Returns the string, or None if empty
    or malformed (a bad time never blocks the save — the date still stands)."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def _accessible_fleets():
    fids = current_user_fleet_ids()
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


def _scoped_citernes():
    q = Citerne.query
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Citerne.fleet_id.in_(fids))
    return q


def _get_citerne_or_404(cid):
    c = db.session.get(Citerne, cid)
    if not c:
        abort(404)
    fids = current_user_fleet_ids()
    if fids is not None and c.fleet_id not in fids:
        abort(403)
    return c


def _accessible_vehicles():
    q = Vehicle.query.filter(Vehicle.is_active.is_(True))
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Vehicle.fleet_id.in_(fids))
    return q.order_by(Vehicle.code).all()


def _entry_vehicles():
    """Vehicles you may record a NEW fuel movement against — the ones on an
    archived fleet drop out (no new data on a mothballed fleet). Consult/history
    scoping keeps using _accessible_vehicles() so past movements stay visible."""
    return [v for v in _accessible_vehicles() if v.fleet is None or v.fleet.is_active]


def _accessible_operators():
    q = Operator.query.filter(Operator.is_active.is_(True))
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Operator.fleet_id.in_(fids))
    return q.order_by(Operator.name).all()


# ── Overview ──────────────────────────────────────────────────────────────────


@carburant_bp.route("/citernes")
@login_required
@require_perm("carburant.view")
def index():
    show_archived = request.args.get("archived") == "1"
    rows = (_scoped_citernes().filter(Citerne.is_active.is_(not show_archived))
            .order_by(Citerne.code).all())
    archived_count = _scoped_citernes().filter(Citerne.is_active.is_(False)).count()
    active_citernes = (_scoped_citernes().filter(Citerne.is_active.is_(True))
                       .order_by(Citerne.code).all())
    cids = [c.id for c in _scoped_citernes().all()]
    vids = [v.id for v in _accessible_vehicles()]
    conds = []
    if cids:
        conds.append(db.and_(FuelMovement.citerne_id.in_(cids),
                             FuelMovement.kind.in_(("rentree", "distribution", "releve", "conso"))))
    if vids:  # direct fills have no citerne — scope them by the vehicle's fleet
        conds.append(db.and_(FuelMovement.kind == "direct",
                             FuelMovement.vehicle_id.in_(vids)))
    recent = []
    if conds:
        recent = (FuelMovement.query.filter(db.or_(*conds))
                  .order_by(FuelMovement.date.desc(), FuelMovement.id.desc())
                  .limit(50).all())
    return render_template(
        "citernes.html", citernes=rows, active_citernes=active_citernes,
        recent=recent, show_archived=show_archived, archived_count=archived_count,
        vehicles=_entry_vehicles(), operators=_accessible_operators(),
        seuil=SEUIL_ECART, today=date.today().isoformat())


MOVEMENT_KINDS = ("rentree", "distribution", "direct", "conso", "releve")


def _last_12_months():
    months, d = [], date.today().replace(day=1)
    for _ in range(12):
        months.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return months


@carburant_bp.route("/citernes/<int:cid>/vue")
@login_required
@require_perm("carburant.view")
def citerne_flux(cid):
    """The animated flow of one citerne for a period: the client fills it
    (rentrée), it burns a little for itself (conso), and it dispenses to the
    machines — the tank level and every figure are the real aggregates."""
    c = _get_citerne_or_404(cid)
    period = request.args.get("period") or date.today().strftime("%Y-%m")

    q = FuelMovement.query.filter_by(citerne_id=cid)
    if period != "all":
        q = q.filter(FuelMovement.date.like(period + "-%"))

    per_vehicle = {}          # vehicle_id -> [vehicle, litres]
    rentree_total = conso_total = distributed_total = 0
    for m in q.all():
        if m.kind == "distribution":
            distributed_total += m.liters
            if m.vehicle:
                row = per_vehicle.setdefault(m.vehicle_id, [m.vehicle, 0])
                row[1] += m.liters
        elif m.kind == "rentree":
            rentree_total += m.liters
        elif m.kind == "conso":
            conso_total += m.liters

    machines = sorted(per_vehicle.values(), key=lambda r: -r[1])
    fill_pct = round(c.stock / c.capacity_liters * 100, 1) if c.capacity_liters else 0

    return render_template(
        "citerne_flux.html", citerne=c, machines=machines, fill_pct=fill_pct,
        rentree_total=rentree_total, conso_total=conso_total,
        distributed_total=distributed_total, period=period, months=_last_12_months())


@carburant_bp.route("/mouvements")
@login_required
@require_perm("carburant.view")
def history():
    """The filterable fuel ledger — every movement, by period / citerne / type,
    with the period totals that make months of data usable."""
    cids = [c.id for c in _scoped_citernes().all()]
    vids = [v.id for v in _accessible_vehicles()]
    conds = []
    if cids:
        conds.append(db.and_(FuelMovement.citerne_id.in_(cids),
                             FuelMovement.kind.in_(("rentree", "distribution", "releve", "conso"))))
    if vids:
        conds.append(db.and_(FuelMovement.kind == "direct",
                             FuelMovement.vehicle_id.in_(vids)))

    ftype = request.args.get("type") or "all"
    fciterne = request.args.get("citerne", type=int)
    period = request.args.get("period") or date.today().strftime("%Y-%m")

    rows, pagination = [], None
    totals = {"rentree": 0, "distribution": 0, "direct": 0, "conso": 0}
    if conds:
        q = FuelMovement.query.filter(db.or_(*conds))
        if ftype in MOVEMENT_KINDS:
            q = q.filter(FuelMovement.kind == ftype)
        if fciterne:
            q = q.filter(FuelMovement.citerne_id == fciterne)
        if period != "all":
            q = q.filter(FuelMovement.date.like(period + "-%"))
        # Litres are summed by the database over everything the filters match.
        # Adding up the rows on screen would have made the totals shrink as the
        # list got longer than a page.
        for kind, litres in (q.with_entities(
                FuelMovement.kind,
                db.func.coalesce(db.func.sum(FuelMovement.liters), 0))
                .group_by(FuelMovement.kind).all()):
            if kind in totals:
                totals[kind] = int(litres or 0)
        pagination = (q.order_by(FuelMovement.date.desc(), FuelMovement.id.desc())
                      .paginate(page=request.args.get("page", 1, type=int),
                                per_page=PER_PAGE, error_out=False))
        rows = pagination.items
    # What the client billed (everything taken at the client) vs what was
    # dispensed to machines from the citernes.
    client_total = totals["rentree"] + totals["direct"] + totals["conso"]

    return render_template(
        "history.html", movements=rows, pagination=pagination,
        totals=totals, client_total=client_total,
        seuil=SEUIL_ECART, months=_last_12_months(), period=period, ftype=ftype,
        fciterne=fciterne, kinds=MOVEMENT_KINDS,
        citernes=_scoped_citernes().order_by(Citerne.code).all())


# ── Citerne CRUD ──────────────────────────────────────────────────────────────


def _read_citerne_form(citerne):
    t = get_t()
    code = (request.form.get("code") or "").strip()
    name = (request.form.get("name") or "").strip()
    cap = request.form.get("capacity_liters", type=int)
    fleet_id = request.form.get("fleet_id", type=int)

    if not code:
        return None, t.get("citerne.err.code", "Le code est obligatoire.")
    if not name:
        return None, t.get("citerne.err.name", "Le nom est obligatoire.")
    if not cap or cap <= 0:
        return None, t.get("citerne.err.capacity", "La capacité doit être positive.")
    if not fleet_id:
        return None, t.get("citerne.err.fleet", "La flotte est obligatoire.")
    fids = current_user_fleet_ids()
    if (fids is not None and fleet_id not in fids) or not db.session.get(Fleet, fleet_id):
        return None, t.get("citerne.err.fleet", "La flotte est obligatoire.")

    clash = Citerne.query.filter(db.func.lower(Citerne.code) == code.lower())
    if citerne:
        clash = clash.filter(Citerne.id != citerne.id)
    if clash.first():
        return None, t.get("citerne.err.code_taken", "Ce code est déjà utilisé.")

    # A citerne starts empty: whatever is in it arrives as a rentrée, dated,
    # like every other litre. There is no opening stock to type in.
    # Shrinking the cuve under what it has held describes a tank that cannot
    # exist, so that is said.
    overflow = max(citerne.max_stock_from(None) - cap, 0) if citerne else 0

    return dict(code=code, name=name, capacity_liters=cap, fleet_id=fleet_id,
                overflow=overflow), None


def _flash_overflow(overflow):
    """Say the tank now reads above its capacity. The entry is kept — this is a
    note, not a refusal. (Under zero is refused outright, never noted.)"""
    if overflow:
        flash("warning|" + get_t().get(
            "citerne.warn_over",
            "Le stock de cette citerne dépasse sa capacité de %(n)d L. "
            "Vérifiez la capacité ou les mouvements.") % {"n": overflow})


def _litres(n):
    """12000 -> "12 000", grouped the way the pages show litres."""
    return "{:,}".format(n).replace(",", " ")


_PHOTO_ERR_KEYS = {
    s3_storage.ERR_TOO_LARGE:      "photo.err.too_large",
    s3_storage.ERR_BAD_FORMAT:     "photo.err.bad_format",
    s3_storage.ERR_NOT_CONFIGURED: "photo.err.not_configured",
    s3_storage.ERR_S3:             "photo.err.s3",
    s3_storage.ERR_UNKNOWN:        "photo.err.unknown",
}


def _apply_citerne_photo_change(citerne):
    """Optional photo on a citerne create/edit POST: a new upload replaces (and
    deletes) the previous object, the "remove" box clears it. Returns a
    localized error string when a submitted photo can't be stored, else None.
    Assumes the citerne row already has an id."""
    t = get_t()
    old_key = citerne.photo_key
    if request.form.get("photo_remove") == "1":
        citerne.photo_key = None
    upload = request.files.get("photo")
    if upload and upload.filename:
        key, err = s3_storage.upload_citerne_photo(upload, citerne.code)
        if err:
            return t.get(_PHOTO_ERR_KEYS.get(err, "photo.err.unknown"),
                         "Photo non enregistrée.")
        citerne.photo_key = key
    if old_key and old_key != citerne.photo_key:
        s3_storage.delete_photo(old_key)
    return None


def _render_citerne_form(citerne, error=None):
    tpl = "_citerne_form.html" if is_modal_request() else "citerne_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, citerne=citerne, error=error,
                           fleets=with_current_fleet(_accessible_fleets(), citerne.fleet if citerne else None)), status


@carburant_bp.route("/citernes/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.manage")
def citerne_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_citerne_form(None)
        if error:
            return _render_citerne_form(None, error)
        data.pop("overflow")         # a new citerne holds nothing yet
        c = Citerne(created_by=current_user.id, **data)
        db.session.add(c)
        db.session.flush()
        perr = _apply_citerne_photo_change(c)
        if perr:
            db.session.rollback()
            return _render_citerne_form(None, perr)
        log_action("CREATE", "citerne", resource_id=c.id, fleet_id=c.fleet_id,
                   detail=f"Created citerne '{c.code}'")
        db.session.commit()
        flash("success|" + t.get("citerne.created", "Citerne créée."))
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_citerne_form(None)


@carburant_bp.route("/citernes/<int:cid>/edit", methods=["GET", "POST"])
@login_required
@require_perm("carburant.manage")
def citerne_edit(cid):
    citerne = _get_citerne_or_404(cid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_citerne_form(citerne)
        if error:
            return _render_citerne_form(citerne, error)
        overflow = data.pop("overflow")
        for k, v in data.items():
            setattr(citerne, k, v)
        perr = _apply_citerne_photo_change(citerne)
        if perr:
            db.session.rollback()
            return _render_citerne_form(citerne, perr)
        log_action("UPDATE", "citerne", resource_id=citerne.id, fleet_id=citerne.fleet_id,
                   detail=f"Updated citerne '{citerne.code}'")
        db.session.commit()
        flash("success|" + t.get("citerne.updated", "Citerne mise à jour."))
        _flash_overflow(overflow)
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_citerne_form(citerne)


@carburant_bp.route("/citernes/<int:cid>/archive", methods=["POST"])
@login_required
@require_perm("carburant.manage")
def citerne_archive(cid):
    citerne = _get_citerne_or_404(cid)
    citerne.is_active = False
    log_action("ARCHIVE", "citerne", resource_id=cid, fleet_id=citerne.fleet_id,
               detail=f"Archived citerne '{citerne.code}'")
    db.session.commit()
    flash("success|" + get_t().get("citerne.archived", "Citerne archivée."))
    return redirect(url_for("carburant.index"))


@carburant_bp.route("/citernes/<int:cid>/reactivate", methods=["POST"])
@login_required
@require_perm("carburant.manage")
def citerne_reactivate(cid):
    citerne = _get_citerne_or_404(cid)
    citerne.is_active = True
    log_action("REACTIVATE", "citerne", resource_id=cid, fleet_id=citerne.fleet_id,
               detail=f"Reactivated citerne '{citerne.code}'")
    db.session.commit()
    flash("success|" + get_t().get("citerne.reactivated", "Citerne réactivée."))
    return redirect(url_for("carburant.index", archived=1))


# ── Distribution (an engin draws fuel from a citerne) ─────────────────────────


def _read_distribution_form():
    """A machine takes fuel — either from a citerne (kind 'distribution') or
    straight at the client with no citerne (kind 'direct', the bus's normal
    case). The 'source' field decides which."""
    t = get_t()
    source = (request.form.get("source") or "citerne").strip()
    fids = current_user_fleet_ids()

    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("distribution.err.date", "Date invalide.")
    time_str = _clean_time(request.form.get("time"))

    v = db.session.get(Vehicle, request.form.get("vehicle_id", type=int) or 0)
    if not v:
        return None, t.get("distribution.err.vehicle", "Choisissez une machine.")
    if fids is not None and v.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")

    liters = request.form.get("liters", type=int)
    if not liters or liters <= 0:
        return None, t.get("distribution.err.liters", "Litres invalides.")

    operator = (request.form.get("operator") or "").strip() or None

    if source == "direct":
        # Straight at the client — no citerne, no reservoir to draw down.
        return dict(kind="direct", citerne_id=None, vehicle_id=v.id, date=date_str,
                    time=time_str, liters=liters, operator=operator), None

    c = db.session.get(Citerne, request.form.get("citerne_id", type=int) or 0)
    if not c or not c.is_active:
        return None, t.get("distribution.err.citerne", "Choisissez une citerne active.")
    if fids is not None and c.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")
    # Deliberately no check that the two are in the same fleet: a tank on site
    # serves whatever turns up at it, and which fleet a machine is booked under
    # is an office arrangement the fuel knows nothing about. Refusing the draw
    # only meant it got written down somewhere else, or not at all. The picker
    # names each machine's fleet, so whoever records it can see what they are
    # doing.
    # No more can leave a tank than is in it. The level that counts is the
    # lowest the tank reaches from this date on, not today's: a draw lowers its
    # own day and every day after, so a back-dated one that fits on its day can
    # still take a later day under zero. A tank already under zero gives
    # nothing until its missing rentrée is recorded.
    available = max(c.min_stock_from(date_str), 0)
    if liters > available:
        if available == 0:
            msg = t.get("distribution.err.empty",
                        "La citerne %(code)s est vide à cette date : aucune distribution "
                        "possible. Si elle a été remplie, enregistrez d'abord la rentrée.")
        else:
            msg = t.get("distribution.err.short",
                        "La citerne %(code)s n'a que %(n)s L disponibles à cette date : "
                        "impossible d'en distribuer %(asked)s L. Si elle a été remplie, "
                        "enregistrez d'abord la rentrée.")
        return None, msg % {"code": c.code, "n": _litres(available),
                            "asked": _litres(liters)}
    return dict(kind="distribution", citerne_id=c.id, vehicle_id=v.id, date=date_str,
                time=time_str, liters=liters, operator=operator), None


def _render_distribution_form(error=None):
    tpl = "_distribution_form.html" if is_modal_request() else "distribution_form.html"
    status = 422 if (error and is_modal_request()) else 200
    citernes = (_scoped_citernes().filter(Citerne.is_active.is_(True))
                .order_by(Citerne.code).all())
    day = request.form.get("date") if _valid_date(request.form.get("date")) else date.today().isoformat()
    return render_template(
        tpl, error=error, citernes=citernes,
        # What each tank can give on the form's date — the same limit the save
        # checks — and its level after every movement, so the page can say it
        # again when the date is changed.
        available={c.id: max(c.min_stock_from(day), 0) for c in citernes},
        levels={c.id: [[m.date, lvl] for m, lvl in c.level_history()] for c in citernes},
        vehicles=_entry_vehicles(), operators=_accessible_operators(),
        today=date.today().isoformat(), now_time=datetime.now().strftime("%H:%M"),
        preset_citerne=request.args.get("citerne", type=int),
        preset_source=request.args.get("source")), status


@carburant_bp.route("/distributions/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.create")
def distribution_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_distribution_form()
        if error:
            return _render_distribution_form(error)
        kind = data.pop("kind")
        c = db.session.get(Citerne, data["citerne_id"]) if data["citerne_id"] else None
        v = db.session.get(Vehicle, data["vehicle_id"])
        mv = FuelMovement(kind=kind, created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        detail = (f"Distribution {mv.liters} L from '{c.code}'" if c
                  else f"Direct fill {mv.liters} L for '{v.code}' at the client")
        log_action("CREATE", "fuel_movement", resource_id=mv.id,
                   fleet_id=(c.fleet_id if c else v.fleet_id), detail=detail)
        db.session.commit()
        flash("success|" + t.get("distribution.created", "Prise de carburant enregistrée."))
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_distribution_form()


# ── Rentrée (the citerne fills up at the client) ──────────────────────────────


def _read_rentree_form():
    t = get_t()
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("rentree.err.date", "Date invalide.")

    c = db.session.get(Citerne, request.form.get("citerne_id", type=int) or 0)
    if not c or not c.is_active:
        return None, t.get("rentree.err.citerne", "Choisissez une citerne active.")
    fids = current_user_fleet_ids()
    if fids is not None and c.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")

    liters = request.form.get("liters", type=int)
    if not liters or liters <= 0:
        return None, t.get("rentree.err.liters", "Litres invalides.")
    # Against the highest level from that date onward, not today's: a fill dated
    # before a stretch where the tank was already full would otherwise overflow
    # it back then, and a legitimate back-dated one gets refused whenever the
    # citerne happens to be full now.
    room = c.capacity_liters - c.max_stock_from(date_str)
    return dict(citerne_id=c.id, date=date_str, liters=liters,
                warn_over=max(liters - room, 0),
                note=(request.form.get("reference") or "").strip() or None), None


def _render_rentree_form(error=None):
    tpl = "_rentree_form.html" if is_modal_request() else "rentree_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error,
        citernes=(_scoped_citernes().filter(Citerne.is_active.is_(True))
                  .order_by(Citerne.code).all()),
        today=date.today().isoformat(),
        preset_citerne=request.args.get("citerne", type=int)), status


@carburant_bp.route("/rentrees/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.create")
def rentree_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_rentree_form()
        if error:
            return _render_rentree_form(error)
        c = db.session.get(Citerne, data["citerne_id"])
        overflow = data.pop("warn_over", 0)
        mv = FuelMovement(kind="rentree", created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id, fleet_id=c.fleet_id,
                   detail=f"Rentrée {mv.liters} L into '{c.code}'")
        db.session.commit()
        flash("success|" + t.get("rentree.created", "Rentrée enregistrée."))
        _flash_overflow(overflow)
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_rentree_form()


# ── Relevé de jauge (physical reading → anomaly check) ────────────────────────


def _read_releve_form():
    t = get_t()
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("releve.err.date", "Date invalide.")

    c = db.session.get(Citerne, request.form.get("citerne_id", type=int) or 0)
    if not c or not c.is_active:
        return None, t.get("releve.err.citerne", "Choisissez une citerne active.")
    fids = current_user_fleet_ids()
    if fids is not None and c.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")

    measured = request.form.get("liters", type=int)
    if measured is None or measured < 0:
        return None, t.get("releve.err.liters", "Relevé invalide.")

    return dict(citerne_id=c.id, date=date_str, liters=measured,
                note=(request.form.get("note") or "").strip() or None), None


def _render_releve_form(error=None):
    tpl = "_releve_form.html" if is_modal_request() else "releve_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error,
        citernes=(_scoped_citernes().filter(Citerne.is_active.is_(True))
                  .order_by(Citerne.code).all()),
        today=date.today().isoformat(),
        preset_citerne=request.args.get("citerne", type=int)), status


@carburant_bp.route("/releves/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.create")
def releve_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_releve_form()
        if error:
            return _render_releve_form(error)
        c = db.session.get(Citerne, data["citerne_id"])
        theoretical = c.stock_as_of(data["date"])
        ecart = theoretical - data["liters"]
        mv = FuelMovement(kind="releve", created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id, fleet_id=c.fleet_id,
                   detail=f"Relevé {mv.liters} L on '{c.code}', écart {ecart:+d} L")
        db.session.commit()
        # Tell the user right away whether the tank reconciles.
        if abs(ecart) > SEUIL_ECART:
            word = t.get("releve.missing", "manquants") if ecart > 0 else t.get("releve.surplus", "en trop")
            flash("error|" + t.get("releve.flag", "Relevé enregistré — écart de %(n)d L %(w)s à vérifier.")
                  % {"n": abs(ecart), "w": word})
        else:
            flash("success|" + t.get("releve.ok", "Relevé enregistré — citerne cohérente."))
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_releve_form()


# ── Conso propre (the citerne takes fuel at the client, for its own engine) ──


def _read_conso_form():
    t = get_t()
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("conso.err.date", "Date invalide.")

    c = db.session.get(Citerne, request.form.get("citerne_id", type=int) or 0)
    if not c or not c.is_active:
        return None, t.get("conso.err.citerne", "Choisissez une citerne active.")
    fids = current_user_fleet_ids()
    if fids is not None and c.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")

    liters = request.form.get("liters", type=int)
    if not liters or liters <= 0:
        return None, t.get("conso.err.liters", "Litres invalides.")

    return dict(citerne_id=c.id, date=date_str, liters=liters,
                operator=(request.form.get("operator") or "").strip() or None), None


def _render_conso_form(error=None):
    tpl = "_conso_form.html" if is_modal_request() else "conso_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error,
        citernes=(_scoped_citernes().filter(Citerne.is_active.is_(True))
                  .order_by(Citerne.code).all()),
        operators=_accessible_operators(), today=date.today().isoformat(),
        preset_citerne=request.args.get("citerne", type=int)), status


@carburant_bp.route("/conso/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.create")
def conso_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_conso_form()
        if error:
            return _render_conso_form(error)
        c = db.session.get(Citerne, data["citerne_id"])
        mv = FuelMovement(kind="conso", created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id, fleet_id=c.fleet_id,
                   detail=f"Conso propre {mv.liters} L for '{c.code}'")
        db.session.commit()
        flash("success|" + t.get("conso.created", "Consommation propre enregistrée."))
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_conso_form()


# ── Ravitaillement (unified VIVO refuel): one action, target decides the kind ──


def _read_ravitaillement_form():
    """One 'take fuel at VIVO' action. The chosen target decides the movement:
        citerne + 'reservoir' -> rentree (fills the tank),
        citerne + 'conso'     -> conso   (the tanker used fuel of its own),
        vehicle               -> direct  (it filled straight at the pump).
    """
    t = get_t()
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t.get("rentree.err.date", "Date invalide.")
    time_str = _clean_time(request.form.get("time"))
    liters = request.form.get("liters", type=int)
    if not liters or liters <= 0:
        return None, t.get("rentree.err.liters", "Litres invalides.")
    operator = (request.form.get("operator") or "").strip() or None
    fids = current_user_fleet_ids()
    target = (request.form.get("target") or "").strip()

    if target.startswith("c:") and target[2:].isdigit():
        c = db.session.get(Citerne, int(target[2:]))
        if not c or not c.is_active:
            return None, t.get("rentree.err.citerne", "Choisissez une citerne active.")
        if fids is not None and c.fleet_id not in fids:
            return None, t.get("error.forbidden", "Action non autorisée.")
        if (request.form.get("fill_type") or "reservoir") == "conso":
            # The citerne took fuel for its own engine, not for its reservoir.
            return dict(kind="conso", citerne_id=c.id, date=date_str, time=time_str,
                        liters=liters, operator=operator), None
        room = c.capacity_liters - c.max_stock_from(date_str)
        return dict(kind="rentree", citerne_id=c.id, date=date_str, time=time_str,
                    liters=liters, operator=operator,
                    warn_over=max(liters - room, 0),
                    note=(request.form.get("reference") or "").strip() or None), None

    if target.startswith("v:") and target[2:].isdigit():
        v = db.session.get(Vehicle, int(target[2:]))
        if not v:
            return None, t.get("distribution.err.vehicle", "Choisissez une machine.")
        if fids is not None and v.fleet_id not in fids:
            return None, t.get("error.forbidden", "Action non autorisée.")
        return dict(kind="direct", vehicle_id=v.id, citerne_id=None, date=date_str,
                    time=time_str, liters=liters, operator=operator), None

    return None, t.get("rav.err.target", "Choisissez une citerne ou un véhicule.")


def _render_ravitaillement_form(error=None):
    tpl = "_ravitaillement_form.html" if is_modal_request() else "ravitaillement_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error,
        citernes=(_scoped_citernes().filter(Citerne.is_active.is_(True))
                  .order_by(Citerne.code).all()),
        vehicles=_entry_vehicles(), operators=_accessible_operators(),
        today=date.today().isoformat(), now_time=datetime.now().strftime("%H:%M"),
        preset_citerne=request.args.get("citerne", type=int)), status


@carburant_bp.route("/ravitaillements/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.create")
def ravitaillement_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_ravitaillement_form()
        if error:
            return _render_ravitaillement_form(error)
        kind = data.pop("kind")
        overflow = data.pop("warn_over", 0)
        c = db.session.get(Citerne, data["citerne_id"]) if data.get("citerne_id") else None
        v = db.session.get(Vehicle, data["vehicle_id"]) if data.get("vehicle_id") else None
        mv = FuelMovement(kind=kind, created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id,
                   fleet_id=(c.fleet_id if c else v.fleet_id),
                   detail=f"Ravitaillement ({kind}) {mv.liters} L")
        db.session.commit()
        flash("success|" + t.get("rav.created", "Ravitaillement enregistré."))
        _flash_overflow(overflow)
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_ravitaillement_form()


# ── Delete a movement (rentrée / distribution / relevé / conso) ───────────────


@carburant_bp.route("/movements/<int:mid>/delete", methods=["POST"])
@login_required
@require_perm("carburant.create")
def movement_delete(mid):
    mv = db.session.get(FuelMovement, mid)
    if not mv or mv.kind not in ("rentree", "distribution", "releve", "conso"):
        abort(404)
    c = db.session.get(Citerne, mv.citerne_id)
    fids = current_user_fleet_ids()
    if fids is not None and c and c.fleet_id not in fids:
        abort(403)
    # A rentrée that later distributions drew on cannot go: without it they
    # took fuel that was never there. The right rentrée goes in first, then the
    # wrong one comes out. Removing a distribution only puts fuel back.
    if c and mv.kind == "rentree":
        floor = c.floor_if_removed(mv.date, mv.liters)
        if floor < 0:
            flash("error|" + get_t().get(
                "movement.err.rentree_needed",
                "Impossible de supprimer cette rentrée : la citerne %(code)s passerait "
                "sous zéro de %(n)s L, car des distributions en dépendent. Enregistrez "
                "d'abord la bonne rentrée, ou supprimez ces distributions.")
                % {"code": c.code, "n": _litres(-floor)})
            return redirect(request.referrer or url_for("carburant.index"))
    over = 0
    if c and mv.kind == "distribution":
        over = max(c.peak_if_returned(mv.date, mv.liters) - c.capacity_liters, 0)
    db.session.delete(mv)
    log_action("DELETE", "fuel_movement", resource_id=mid,
               fleet_id=(c.fleet_id if c else None), detail=f"Deleted {mv.kind}")
    db.session.commit()
    flash("success|" + get_t().get("movement.deleted", "Mouvement supprimé."))
    _flash_overflow(over)
    return redirect(request.referrer or url_for("carburant.index"))
