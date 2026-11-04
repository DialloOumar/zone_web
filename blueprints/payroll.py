"""Pay settings -- what the law says, dated, kept by the super admin.

Two lists on one page:
- the versions of the legal figures (CNSS rates, ceiling and floor, RTS
  scale, employer's taxes), each in force from its own day: a change of law
  is a new version, so a month is always worked out with the figures of its
  time (payroll.payslip);
- the kinds of allowance paid on top of the base salary, with whether each is
  taxed and whether the CNSS is due on it.
"""
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import get_t, is_modal_request, log_action, modal_ok, parse_amount, super_admin_required
from models import PayAllowance, PayRtsBracket, PaySetting, StaffSalaryAllowance, db

payroll_bp = Blueprint("payroll", __name__)

TABS = ("versions", "allowances")


def _valid_day(s):
    from datetime import datetime
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _rate(name):
    """A percent typed with a point or a comma: Decimal, or None if wrong."""
    raw = (request.form.get(name) or "").strip().replace(",", ".").replace("%", "").strip()
    if raw == "":
        return Decimal(0)
    try:
        d = Decimal(raw)
    except InvalidOperation:
        return None
    return d if 0 <= d <= 100 else None


def _money(name):
    """(amount or None when empty, ok)."""
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return None, True
    v = parse_amount(raw)
    return v, v is not None and v >= 0


@payroll_bp.route("/paie/parametres")
@login_required
@super_admin_required
def settings():
    tab = request.args.get("tab") if request.args.get("tab") in TABS else "versions"
    used = {r[0] for r in db.session.query(StaffSalaryAllowance.allowance_id).distinct().all()}
    return render_template(
        "pay_settings.html", tab=tab,
        versions=PaySetting.query.order_by(PaySetting.effective_on.desc()).all(),
        current=PaySetting.on(_today()), today_iso=_today(),
        allowances=PayAllowance.query.order_by(PayAllowance.sort_order, PayAllowance.name).all(),
        used=used)


def _today():
    from datetime import date
    return date.today().isoformat()


def _read_version(row):
    """Saves the form into a version (a new one when row is None). Returns the
    error, or None once saved."""
    t = get_t()
    day = (request.form.get("effective_on") or "").strip()
    if not _valid_day(day):
        return t["payset.err.day"]
    clash = PaySetting.query.filter(PaySetting.effective_on == day)
    if row is not None:
        clash = clash.filter(PaySetting.id != row.id)
    if clash.first():
        return t["payset.err.day_taken"]
    rates = {}
    for name in ("cnss_employee_rate", "cnss_employer_rate", "vf_rate", "ta_rate"):
        rates[name] = _rate(name)
        if rates[name] is None:
            return t["payset.err.rate"]
    ceiling, ok1 = _money("cnss_ceiling")
    floor, ok2 = _money("cnss_floor")
    if not (ok1 and ok2):
        return t["payset.err.amount"]
    if ceiling and floor and floor > ceiling:
        return t["payset.err.floor"]
    # The RTS scale: one step per row, "from" and rate, sorted by "from".
    steps = []
    for lower_raw, rate_raw in zip(request.form.getlist("rts_lower"), request.form.getlist("rts_rate")):
        lower_raw, rate_raw = lower_raw.strip(), rate_raw.strip().replace(",", ".").replace("%", "").strip()
        if not lower_raw and not rate_raw:
            continue
        lower = parse_amount(lower_raw) if lower_raw else 0
        try:
            rate = Decimal(rate_raw)
        except InvalidOperation:
            return t["payset.err.rts"]
        if lower is None or lower < 0 or not 0 <= rate <= 100:
            return t["payset.err.rts"]
        steps.append((lower, rate))
    steps.sort()
    if not steps:
        return t["payset.err.rts_empty"]
    if len({s[0] for s in steps}) != len(steps):
        return t["payset.err.rts"]
    if steps[0][0] != 0:
        steps.insert(0, (0, Decimal(0)))
    if row is None:
        row = PaySetting(created_by=current_user.id)
        db.session.add(row)
    row.effective_on = day
    for k, v in rates.items():
        setattr(row, k, v)
    row.cnss_ceiling, row.cnss_floor = ceiling, floor
    row.note = (request.form.get("note") or "").strip()[:255] or None
    row.brackets = [PayRtsBracket(lower=lo, rate=ra) for lo, ra in steps]
    return None


def _render_version(row, error=None, source=None):
    return render_template("pay_setting_form.html", row=row, error=error,
                           source=source if row is None else row)


@payroll_bp.route("/paie/parametres/version/nouvelle", methods=["GET", "POST"])
@login_required
@super_admin_required
def version_new():
    # A new version starts as a copy of the latest: only what the law changed
    # is typed.
    latest = PaySetting.query.order_by(PaySetting.effective_on.desc()).first()
    if request.method == "POST":
        error = _read_version(None)
        if error:
            return _render_version(None, error, latest)
        log_action("CREATE", "pay_setting", detail="Pay settings from %s" % request.form.get("effective_on"))
        db.session.commit()
        flash("success|" + get_t()["payset.created"])
        return redirect(url_for("payroll.settings"))
    return _render_version(None, source=latest)


@payroll_bp.route("/paie/parametres/version/<int:vid>/modifier", methods=["GET", "POST"])
@login_required
@super_admin_required
def version_edit(vid):
    row = db.session.get(PaySetting, vid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _read_version(row)
        if error:
            db.session.rollback()
            return _render_version(db.session.get(PaySetting, vid), error)
        log_action("UPDATE", "pay_setting", resource_id=row.id,
                   detail="Corrected pay settings from %s" % row.effective_on)
        db.session.commit()
        flash("success|" + get_t()["payset.updated"])
        return redirect(url_for("payroll.settings"))
    return _render_version(row)


@payroll_bp.route("/paie/parametres/version/<int:vid>/supprimer", methods=["POST"])
@login_required
@super_admin_required
def version_delete(vid):
    row = db.session.get(PaySetting, vid)
    if not row:
        abort(404)
    log_action("DELETE", "pay_setting", resource_id=vid, detail="Deleted pay settings from %s" % row.effective_on)
    db.session.delete(row)
    db.session.commit()
    flash("success|" + get_t()["payset.deleted"])
    return redirect(url_for("payroll.settings"))


def _allowance_form(row):
    t = get_t()
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:80]
        clash = PayAllowance.query.filter(db.func.lower(PayAllowance.name) == name.lower())
        if row is not None:
            clash = clash.filter(PayAllowance.id != row.id)
        if not name:
            error = t["list.err.name_required"]
        elif clash.first():
            error = t["list.err.name_taken"]
        else:
            if row is None:
                nxt = (db.session.query(db.func.max(PayAllowance.sort_order)).scalar() or 0) + 1
                row = PayAllowance(sort_order=nxt, is_active=True)
                db.session.add(row)
            else:
                row.is_active = request.form.get("is_active") == "1"
            row.name = name
            row.taxable = request.form.get("taxable") == "1"
            row.cnss = request.form.get("cnss") == "1"
            log_action("UPDATE", "pay_allowance", detail="Allowance '%s'" % name)
            db.session.commit()
            flash("success|" + t["payset.allowance_saved"])
            return modal_ok() if is_modal_request() else redirect(url_for("payroll.settings", tab="allowances"))
    tpl = "_pay_allowance_form.html" if is_modal_request() else "pay_allowance_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


@payroll_bp.route("/paie/indemnites/nouvelle", methods=["GET", "POST"])
@login_required
@super_admin_required
def allowance_new():
    return _allowance_form(None)


@payroll_bp.route("/paie/indemnites/<int:aid>/modifier", methods=["GET", "POST"])
@login_required
@super_admin_required
def allowance_edit(aid):
    row = db.session.get(PayAllowance, aid)
    if not row:
        abort(404)
    return _allowance_form(row)
