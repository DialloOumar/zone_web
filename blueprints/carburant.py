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


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


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

    rows = []
    if conds:
        q = FuelMovement.query.filter(db.or_(*conds))
        if ftype in MOVEMENT_KINDS:
            q = q.filter(FuelMovement.kind == ftype)
        if fciterne:
            q = q.filter(FuelMovement.citerne_id == fciterne)
        if period != "all":
            q = q.filter(FuelMovement.date.like(period + "-%"))
        rows = q.order_by(FuelMovement.date.desc(), FuelMovement.id.desc()).limit(1000).all()

    totals = {"rentree": 0, "distribution": 0, "direct": 0, "conso": 0}
    for m in rows:
        if m.kind in totals:
            totals[m.kind] += m.liters
    # What the client billed (everything taken at the client) vs what was
    # dispensed to machines from the citernes.
    client_total = totals["rentree"] + totals["direct"] + totals["conso"]

    return render_template(
        "history.html", movements=rows, totals=totals, client_total=client_total,
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
    initial = request.form.get("initial_liters", type=int) or 0

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
    if initial < 0:
        initial = 0
    if initial > cap:
        return None, t.get("citerne.err.over_capacity", "Le stock dépasse la capacité.")

    clash = Citerne.query.filter(db.func.lower(Citerne.code) == code.lower())
    if citerne:
        clash = clash.filter(Citerne.id != citerne.id)
    if clash.first():
        return None, t.get("citerne.err.code_taken", "Ce code est déjà utilisé.")

    # Optional link to the tanker's own vehicle (must be in the same fleet).
    vehicle_id = request.form.get("vehicle_id", type=int) or None
    if vehicle_id:
        v = db.session.get(Vehicle, vehicle_id)
        if not v or v.fleet_id != fleet_id:
            return None, t.get("citerne.err.vehicle",
                               "Le camion-citerne doit être un véhicule de la même flotte.")

    return dict(code=code, name=name, capacity_liters=cap, fleet_id=fleet_id,
                vehicle_id=vehicle_id, initial=initial), None


def _set_initial_stock(citerne, liters, today):
    """The citerne's opening stock is a single 'initial' movement, so editing
    it just adjusts (or removes) that one row."""
    mv = next((m for m in citerne.movements if m.kind == "initial"), None)
    if liters > 0:
        if mv:
            mv.liters = liters
        else:
            db.session.add(FuelMovement(
                citerne_id=citerne.id, kind="initial", date=today,
                liters=liters, created_by=current_user.id))
    elif mv:
        db.session.delete(mv)


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
                           fleets=with_current_fleet(_accessible_fleets(), citerne.fleet if citerne else None),
                           vehicles=_entry_vehicles()), status


@carburant_bp.route("/citernes/new", methods=["GET", "POST"])
@login_required
@require_perm("carburant.manage")
def citerne_new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_citerne_form(None)
        if error:
            return _render_citerne_form(None, error)
        initial = data.pop("initial")
        c = Citerne(created_by=current_user.id, **data)
        db.session.add(c)
        db.session.flush()
        perr = _apply_citerne_photo_change(c)
        if perr:
            db.session.rollback()
            return _render_citerne_form(None, perr)
        _set_initial_stock(c, initial, date.today().isoformat())
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
        initial = data.pop("initial")
        for k, v in data.items():
            setattr(citerne, k, v)
        perr = _apply_citerne_photo_change(citerne)
        if perr:
            db.session.rollback()
            return _render_citerne_form(citerne, perr)
        _set_initial_stock(citerne, initial, date.today().isoformat())
        log_action("UPDATE", "citerne", resource_id=citerne.id, fleet_id=citerne.fleet_id,
                   detail=f"Updated citerne '{citerne.code}'")
        db.session.commit()
        flash("success|" + t.get("citerne.updated", "Citerne mise à jour."))
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
                    liters=liters, operator=operator), None

    c = db.session.get(Citerne, request.form.get("citerne_id", type=int) or 0)
    if not c or not c.is_active:
        return None, t.get("distribution.err.citerne", "Choisissez une citerne active.")
    if fids is not None and c.fleet_id not in fids:
        return None, t.get("error.forbidden", "Action non autorisée.")
    if v.fleet_id != c.fleet_id:
        return None, t.get("distribution.err.fleet_mismatch",
                           "La machine et la citerne doivent être de la même flotte.")
    if liters > c.stock:
        return None, t.get("distribution.err.stock",
                           "Stock insuffisant dans la citerne (%d L disponibles)." % c.stock)

    return dict(kind="distribution", citerne_id=c.id, vehicle_id=v.id, date=date_str,
                liters=liters, operator=operator), None


def _render_distribution_form(error=None):
    tpl = "_distribution_form.html" if is_modal_request() else "distribution_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(
        tpl, error=error,
        citernes=(_scoped_citernes().filter(Citerne.is_active.is_(True))
                  .order_by(Citerne.code).all()),
        vehicles=_entry_vehicles(), operators=_accessible_operators(),
        today=date.today().isoformat(),
        preset_citerne=request.args.get("citerne", type=int)), status


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
    if c.stock + liters > c.capacity_liters:
        return None, t.get("rentree.err.over_capacity",
                           "Cette rentrée dépasse la capacité (%d L max, %d L déjà en cuve)."
                           % (c.capacity_liters, c.stock))

    return dict(citerne_id=c.id, date=date_str, liters=liters,
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
        mv = FuelMovement(kind="rentree", created_by=current_user.id, **data)
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id, fleet_id=c.fleet_id,
                   detail=f"Rentrée {mv.liters} L into '{c.code}'")
        db.session.commit()
        flash("success|" + t.get("rentree.created", "Rentrée enregistrée."))
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
        # If the citerne is tied to a tanker vehicle, the conso is that
        # vehicle's own fuel — attribute it so it lands on the vehicle too.
        if c.vehicle_id:
            mv.vehicle_id = c.vehicle_id
        db.session.add(mv)
        db.session.flush()
        log_action("CREATE", "fuel_movement", resource_id=mv.id, fleet_id=c.fleet_id,
                   detail=f"Conso propre {mv.liters} L for '{c.code}'")
        db.session.commit()
        flash("success|" + t.get("conso.created", "Consommation propre enregistrée."))
        return modal_ok() if is_modal_request() else redirect(url_for("carburant.index"))
    return _render_conso_form()


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
    # Removing a rentrée must not push the tank below zero.
    if mv.kind == "rentree" and c and c.stock - mv.liters < 0:
        flash("error|" + get_t().get("rentree.err.delete_negative",
              "Suppression impossible : le stock deviendrait négatif."))
        return redirect(request.referrer or url_for("carburant.index"))
    db.session.delete(mv)
    log_action("DELETE", "fuel_movement", resource_id=mid,
               fleet_id=(c.fleet_id if c else None), detail=f"Deleted {mv.kind}")
    db.session.commit()
    flash("success|" + get_t().get("movement.deleted", "Mouvement supprimé."))
    return redirect(request.referrer or url_for("carburant.index"))
