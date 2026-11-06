"""Pay settings -- what the law says, dated, kept by the super admin.

Three lists on one page:
- the versions of the legal figures (CNSS rates, ceiling and floor, RTS
  scale, employer's taxes), each in force from its own day: a change of law
  is a new version, so a month is always worked out with the figures of its
  time (payroll.payslip);
- the kinds of allowance paid on top of the base salary, with whether each is
  taxed and whether the CNSS is due on it;
- the banks employees are paid into, one spelling each, with the bank code
  that opens a RIB.
"""
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import (_user_approves_payroll, get_t, has_perm, is_modal_request, log_action, modal_ok,
                 parse_amount, payroll_approver_exists, require_perm, super_admin_required)
from payroll import box_lines, freeze, run_lines, transfer_lines
from models import Bank, CashAccount, LedgerAccount, PayAllowance, PayRun, PayRunAdjustment, PayRtsBracket, PaySetting, Staff, StaffSalaryAllowance, db

payroll_bp = Blueprint("payroll", __name__)

TABS = ("versions", "allowances", "banks")

# The account a pay item is charged to when none was set on it: SYSCOHADA's
# "other allowances" for a monthly one, "bonuses" for one paid once.
DEFAULT_CODES = {"allowance": "6638", "bonus": "6612"}


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
        used=used, default_codes=DEFAULT_CODES,
        banks=Bank.query.order_by(Bank.sort_order, Bank.name).all(),
        bank_people={bid: n for bid, n in db.session.query(Staff.bank_id, db.func.count(Staff.id))
                     .filter(Staff.bank_id.isnot(None)).group_by(Staff.bank_id).all()})


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


def _plan_code(raw):
    """(code or None, ok): an account of the plan, any of them -- payroll
    reaches accounts the costs never use. Chosen once, it joins the shortlist
    so the journal names it."""
    code = (raw or "").strip()
    if not code:
        return None, True
    acc = LedgerAccount.query.filter_by(code=code, is_active=True).first()
    if acc is None:
        return None, False
    if not acc.is_used:
        acc.is_used = True
    return acc.code, True


def _allowance_form(row):
    t = get_t()
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:80]
        clash = PayAllowance.query.filter(db.func.lower(PayAllowance.name) == name.lower())
        if row is not None:
            clash = clash.filter(PayAllowance.id != row.id)
        code, code_ok = _plan_code(request.form.get("ledger_code"))
        if not name:
            error = t["list.err.name_required"]
        elif clash.first():
            error = t["list.err.name_taken"]
        elif not code_ok:
            error = t["payset.err.code"]
        else:
            if row is None:
                nxt = (db.session.query(db.func.max(PayAllowance.sort_order)).scalar() or 0) + 1
                row = PayAllowance(sort_order=nxt, is_active=True)
                db.session.add(row)
            else:
                row.is_active = request.form.get("is_active") == "1"
            row.name = name
            row.kind = request.form.get("kind") if request.form.get("kind") in PayAllowance.KINDS else "allowance"
            row.taxable = request.form.get("taxable") == "1"
            row.cnss = request.form.get("cnss") == "1"
            row.ledger_code = code
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


# ── The banks ───────────────────────────────────────────────────────────────


def _bank_form(row):
    t = get_t()
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:80]
        code = (request.form.get("code") or "").replace(" ", "").strip()[:10] or None
        clash = Bank.query.filter(db.func.lower(Bank.name) == name.lower())
        if row is not None:
            clash = clash.filter(Bank.id != row.id)
        if not name:
            error = t["list.err.name_required"]
        elif clash.first():
            error = t["list.err.name_taken"]
        elif code and not code.isdigit():
            error = t["pay.err.digits"]
        else:
            if row is None:
                nxt = (db.session.query(db.func.max(Bank.sort_order)).scalar() or 0) + 1
                row = Bank(sort_order=nxt, is_active=True)
                db.session.add(row)
            else:
                row.is_active = request.form.get("is_active") == "1"
            row.name, row.code = name, code
            log_action("UPDATE", "bank", detail="Bank '%s' (%s)" % (name, code or "-"))
            db.session.commit()
            flash("success|" + t["pay.bank_saved"])
            return modal_ok() if is_modal_request() else redirect(url_for("payroll.settings", tab="banks"))
    tpl = "_bank_form.html" if is_modal_request() else "bank_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


@payroll_bp.route("/paie/banques/nouvelle", methods=["GET", "POST"])
@login_required
@super_admin_required
def bank_new():
    return _bank_form(None)


@payroll_bp.route("/paie/banques/<int:bid>/modifier", methods=["GET", "POST"])
@login_required
@super_admin_required
def bank_edit(bid):
    row = db.session.get(Bank, bid)
    if not row:
        abort(404)
    return _bank_form(row)


# ── The month's pay ─────────────────────────────────────────────────────────
#
# One per month. Prepared from the files as they stand, with the month's
# adjustments; then closed -- straight away while no role holds the
# "approves_payroll" box, else submitted and validated by a holder. Closing
# freezes every employee's figures (payroll.freeze); from then on the list,
# the payslips and the profitability read those.

RUN_TABS = ("list", "changes", "adjustments", "payment")


def _may_view_runs():
    return has_perm("payroll.view") or has_perm("payroll.manage") or _user_approves_payroll()


def _get_run_or_404(month):
    run = PayRun.query.filter_by(month=month).first()
    if run is None or not _may_view_runs():
        abort(404)
    return run


def _month_label(month):
    from app import MONTH_NAMES, current_lang
    names = MONTH_NAMES.get(current_lang(), MONTH_NAMES["en"])
    return "%s %s" % (names[int(month[5:]) - 1].capitalize(), month[:4])


def _summary(rows):
    """The month's totals, and the net split by how it reaches people."""
    ok = [s for _, s, e in rows if s is not None]
    by = {"transfer": 0, "box": 0, "none": 0}
    for s in ok:
        how = s["who"]["pay_method"]
        by["transfer" if how == "transfer" else "box" if how in ("mobile", "cash") else "none"] += s["net"]
    return dict(people=len(ok), errors=len(rows) - len(ok),
                gross=sum(s["gross"] for s in ok), net=sum(s["net"] for s in ok),
                cost=sum(s["cost"] for s in ok), by=by)


def _groups(rows):
    """The list by department, each with its sub-totals."""
    out = []
    for person, slip, err in rows:
        dept = (slip or {}).get("who", {}).get("department") or (person.department.name if person.department else None)
        if not out or out[-1]["name"] != dept:
            out.append(dict(name=dept, rows=[], gross=0, taxes=0, taken=0, net=0))
        g = out[-1]
        g["rows"].append((person, slip, err))
        if slip:
            g["gross"] += slip["gross"]
            g["taxes"] += slip["cnss_employee"] + slip["rts"]
            g["taken"] += slip["advances_total"] + slip["prepaid_total"] + slip["other_total"]
            g["net"] += slip["net"]
    return out


def _changes(run, rows):
    """What moved since the month before: who came, who left, whose gross
    changed, the cash box's advances and bonuses, the adjustments, and who
    cannot be paid for lack of a way to pay him."""
    y, m = int(run.month[:4]), int(run.month[5:])
    prev_month = "%04d-%02d" % ((y, m - 1) if m > 1 else (y - 1, 12))
    prev = PayRun.query.filter_by(month=prev_month, status="validated").first()
    before = {l.staff_id: json.loads(l.slip) for l in prev.lines} if prev else None
    now = {p.id: (p, s) for p, s, e in rows if s}
    out = []
    if before is not None:
        for pid, (p, s) in now.items():
            if pid not in before:
                out.append(dict(person=p, what="new", before=None, after=s["gross"]))
            elif before[pid]["gross"] != s["gross"]:
                out.append(dict(person=p, what="gross", before=before[pid]["gross"], after=s["gross"]))
        for pid, s in before.items():
            if pid not in now:
                out.append(dict(person=db.session.get(Staff, pid), what="gone", before=s["gross"], after=None))
    for pid, (p, s) in now.items():
        if s["advances_total"]:
            out.append(dict(person=p, what="advance", before=None, after=s["advances_total"]))
        if s["prepaid_total"]:
            out.append(dict(person=p, what="bonus", before=None, after=s["prepaid_total"]))
        how = s["who"]["pay_method"]
        if not how or (how == "transfer" and not s["who"]["bank_account"]):
            out.append(dict(person=p, what="no_way", before=None, after=None))
    for p, s, e in rows:
        if e:
            out.append(dict(person=p, what="error", before=None, after=None, error=e))
    return out, prev is not None


@payroll_bp.route("/paie")
@login_required
def runs():
    if not _may_view_runs():
        abort(403)
    runs = PayRun.query.order_by(PayRun.month.desc()).all()
    totals = {}
    for r in runs:
        if r.status == "validated":
            totals[r.id] = dict(people=len(r.lines), net=sum(l.net for l in r.lines),
                                cost=sum(l.cost for l in r.lines))
    from datetime import date
    taken = {r.month for r in runs}
    month = date.today().strftime("%Y-%m")
    return render_template("pay_runs.html", runs=runs, totals=totals, labels={r.month: _month_label(r.month) for r in runs},
                           next_month=month if month not in taken else "", can_manage=has_perm("payroll.manage"))


@payroll_bp.route("/paie/nouvelle", methods=["POST"])
@login_required
@require_perm("payroll.manage")
def run_new():
    month = (request.form.get("month") or "").strip()
    t = get_t()
    if not (len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()
            and 1 <= int(month[5:]) <= 12):
        flash("error|" + t["run.err.month"])
        return redirect(url_for("payroll.runs"))
    if PayRun.query.filter_by(month=month).first():
        return redirect(url_for("payroll.run", month=month))
    db.session.add(PayRun(month=month, created_by=current_user.id))
    log_action("CREATE", "pay_run", detail="Pay of %s opened" % month)
    db.session.commit()
    return redirect(url_for("payroll.run", month=month))


@payroll_bp.route("/paie/<month>")
@login_required
def run(month):
    r = _get_run_or_404(month)
    tab = request.args.get("tab") if request.args.get("tab") in RUN_TABS else "list"
    rows = run_lines(r)
    changes, compared = _changes(r, rows) if tab == "changes" else ([], False)
    # Paying a validated month: by the bank, and by the cash box.
    transfers = box = accounts = None
    if r.status == "validated":
        transfers, box = transfer_lines(r), box_lines(r)
        accounts = _company_bank_accounts()
    return render_template(
        "pay_run.html", run=r, tab=tab, label=_month_label(month), rows=rows,
        groups=_groups(rows), summary=_summary(rows), changes=changes, compared=compared,
        approver_exists=payroll_approver_exists(), approves=_user_approves_payroll(),
        can_manage=has_perm("payroll.manage"), may_pay=_may_pay(),
        transfers=transfers, box=box, accounts=accounts, pay_account=_pay_account(r) if accounts else None,
        today=date.today().isoformat())


@payroll_bp.route("/paie/<month>/etat")
@login_required
def run_print(month):
    """The month's salary list, by department, to print or keep as PDF."""
    r = _get_run_or_404(month)
    rows = run_lines(r)
    from blueprints.invoicing import invoice_settings
    return render_template("pay_run_print.html", run=r, label=_month_label(month),
                           groups=_groups(rows), summary=_summary(rows), co=invoice_settings())


def _run_people(r):
    """Who an adjustment can be for: the month's employees."""
    return [p for p, s, e in run_lines(r)]


def _adjustment_form(r, row):
    t = get_t()
    error = None
    if r.locked:
        abort(403)
    if request.method == "POST":
        staff_id = request.form.get("staff_id", type=int)
        kind = request.form.get("kind")
        label = (request.form.get("label") or "").strip()[:120]
        amount = parse_amount(request.form.get("amount"))
        if staff_id not in {p.id for p in _run_people(r)}:
            error = t["advance.err.person"]
        elif kind not in PayRunAdjustment.KINDS:
            error = t["adj.err.kind"]
        elif not label:
            error = t["adj.err.label"]
        elif amount is None or amount <= 0:
            error = t["expense.err.amount_required"]
        else:
            if row is None:
                row = PayRunAdjustment(run_id=r.id, created_by=current_user.id)
                db.session.add(row)
            row.staff_id, row.kind, row.label, row.amount = staff_id, kind, label, amount
            row.taxable = kind != "net" and request.form.get("taxable") == "1"
            row.cnss = kind != "net" and request.form.get("cnss") == "1"
            log_action("UPDATE", "pay_run", resource_id=r.id,
                       detail="Adjustment %s %d GNF (%s) for staff %d, pay of %s" % (kind, amount, label, staff_id, r.month))
            db.session.commit()
            flash("success|" + t["adj.saved"])
            return modal_ok() if is_modal_request() else redirect(url_for("payroll.run", month=r.month, tab="adjustments"))
    tpl = "_pay_adjustment_form.html" if is_modal_request() else "pay_adjustment_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, run=r, row=row, error=error, people=_run_people(r)), status


@payroll_bp.route("/paie/<month>/ajustements/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("payroll.manage")
def run_adjustment_new(month):
    return _adjustment_form(_get_run_or_404(month), None)


@payroll_bp.route("/paie/ajustements/<int:aid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("payroll.manage")
def run_adjustment_edit(aid):
    row = db.session.get(PayRunAdjustment, aid)
    if row is None:
        abort(404)
    return _adjustment_form(row.run, row)


@payroll_bp.route("/paie/ajustements/<int:aid>/supprimer", methods=["POST"])
@login_required
@require_perm("payroll.manage")
def run_adjustment_delete(aid):
    row = db.session.get(PayRunAdjustment, aid)
    if row is None:
        abort(404)
    r = row.run
    if r.locked:
        abort(403)
    db.session.delete(row)
    log_action("UPDATE", "pay_run", resource_id=r.id, detail="Adjustment removed from the pay of %s" % r.month)
    db.session.commit()
    flash("success|" + get_t()["adj.deleted"])
    return redirect(url_for("payroll.run", month=r.month, tab="adjustments"))


def _close(r):
    """The month is locked: every employee's figures frozen. Refused while a
    line cannot be worked out (no salary settings for the month...)."""
    rows = run_lines(r)
    if any(e for _, _, e in rows):
        return get_t()["run.err.lines"]
    freeze(r)
    r.status = "validated"
    r.validated_by, r.validated_at = current_user.id, datetime.utcnow()
    r.note = None
    return None


@payroll_bp.route("/paie/<month>/<any(soumettre,valider,renvoyer,supprimer):what>", methods=["POST"])
@login_required
def run_action(month, what):
    """submit -- RH hands it over; validate -- the holder of the box (or RH
    alone while nobody holds it) locks it; send back -- to draft, with why;
    delete -- a draft opened by mistake."""
    r = _get_run_or_404(month)
    t = get_t()
    manage, approves = has_perm("payroll.manage"), _user_approves_payroll()
    error = None
    if what == "soumettre":
        if not manage or r.status != "draft":
            abort(403)
        if not payroll_approver_exists():
            error = _close(r)
        elif any(e for _, _, e in run_lines(r)):
            error = t["run.err.lines"]
        else:
            r.status, r.submitted_by, r.submitted_at = "submitted", current_user.id, datetime.utcnow()
    elif what == "valider":
        if r.status == "submitted" and approves:
            error = _close(r)
        elif r.status == "draft" and manage and not payroll_approver_exists():
            error = _close(r)
        else:
            abort(403)
    elif what == "renvoyer":
        if r.status != "submitted" or not approves:
            abort(403)
        r.status, r.note = "draft", (request.form.get("note") or "").strip()[:255] or None
    else:
        if r.status != "draft" or not manage:
            abort(403)
        db.session.delete(r)
        log_action("DELETE", "pay_run", detail="Pay of %s deleted" % month)
        db.session.commit()
        flash("success|" + t["run.deleted"])
        return redirect(url_for("payroll.runs"))
    if error:
        db.session.rollback()
        flash("error|" + error)
    else:
        log_action("UPDATE", "pay_run", resource_id=r.id, detail="Pay of %s: %s" % (month, what))
        db.session.commit()
        flash("success|" + t["run.done." + what])
    return redirect(url_for("payroll.run", month=month))


# ── Paying the month ────────────────────────────────────────────────────────
#
# A validated month leaves in two ways. By bank: the transfer file and the
# letter for the company's bank, then "virement effectué" once the bank has
# carried it out -- the total leaves the company account in Comptes that day.
# By the cash box: the Orange Money and cash people, ticked one by one in the
# Caisse as they are paid (expenses.salaries).

def _may_pay():
    return has_perm("payroll.manage") or _user_approves_payroll() or has_perm("accounting.manage")


def _validated_or_404(month):
    r = _get_run_or_404(month)
    if r.status != "validated" or not _may_pay():
        abort(404)
    return r


def _company_bank_accounts():
    return (CashAccount.query.filter_by(is_active=True, is_repayable=False, kind="bank")
            .order_by(CashAccount.sort_order, CashAccount.name).all())


def _pay_account(r):
    """The account the month is paid from: the one chosen, else the BSIC
    account, else the first company bank account."""
    if r.pay_account is not None:
        return r.pay_account
    accounts = _company_bank_accounts()
    return next((a for a in accounts if "bsic" in a.name.lower()), accounts[0] if accounts else None)


def _libelle(month):
    """The transfer's wording as the bank's file carries it: SALAIREOCTOBRE2026."""
    import unicodedata
    word = unicodedata.normalize("NFKD", _month_label(month).split()[0]).encode("ascii", "ignore").decode()
    return ("SALAIRE" + word + month[:4]).upper()


@payroll_bp.route("/paie/<month>/compte", methods=["POST"])
@login_required
def run_account(month):
    """The company account the month's transfers leave from."""
    r = _validated_or_404(month)
    if r.transfer_on:
        abort(403)
    acc = db.session.get(CashAccount, request.form.get("account_id", type=int) or 0)
    if acc is None or acc not in _company_bank_accounts():
        flash("error|" + get_t()["run.err.account"])
    else:
        r.pay_account_id = acc.id
        log_action("UPDATE", "pay_run", resource_id=r.id, detail="Pay of %s paid from '%s'" % (month, acc.name))
        db.session.commit()
    return redirect(url_for("payroll.run", month=month, tab="payment"))


@payroll_bp.route("/paie/<month>/virements.xlsx")
@login_required
def run_transfer_file(month):
    """The list for the bank, laid out like the file sent today: who, bank,
    bank code, branch, account, key, amount, the transfer's wording."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from flask import send_file
    r = _validated_or_404(month)
    rows = transfer_lines(r)
    from blueprints.invoicing import invoice_settings
    co = invoice_settings()
    wb = Workbook()
    ws = wb.active
    ws.title = _libelle(month)[:31]
    acc = _pay_account(r)
    ws["A1"] = co.get("company_name") or ""
    ws["A1"].font = Font(bold=True, size=12)
    ws["A2"] = "%s%s" % (get_t()["letter.list"] % _month_label(month),
                         (" — %s %s" % (acc.name, acc.number or "")) if acc else "")
    head = ["N°", "NOMS ET PRENOMS", "BANQUE", "CODE BANQUE", "CODE AGENCE", "N° COMPTE", "CLE RIB", "SALAIRE", "LIBELLE"]
    ws.append([])
    ws.append(head)
    for cell in ws[4]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EEF8")
        cell.alignment = Alignment(horizontal="center")
    for i, row in enumerate(rows, 1):
        w = row["who"]
        ws.append([i, w["name"], w["bank"] or "", w["bank_code"] or "", w["bank_branch"] or "",
                   w["bank_account"] or "", w["bank_key"] or "", row["net"], _libelle(month)])
    last = ws.max_row
    ws.append(["", "TOTAL", "", "", "", "", "", "=SUM(H5:H%d)" % last if rows else 0, ""])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for col, width in zip("ABCDEFGHI", (5, 34, 16, 12, 12, 18, 8, 16, 22)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=5, min_col=8, max_col=8):
        for cell in row:
            cell.number_format = "#,##0"
    # Codes stay text: a leading zero (002) is part of a bank code.
    for row in ws.iter_rows(min_row=5, min_col=4, max_col=7):
        for cell in row:
            cell.number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    log_action("EXPORT", "pay_run", resource_id=r.id, detail="Transfer file of %s" % month)
    db.session.commit()
    return send_file(buf, as_attachment=True, download_name="virements-%s.xlsx" % month,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@payroll_bp.route("/paie/<month>/lettre")
@login_required
def run_letter(month):
    """The letter to the company's bank, with the list after it."""
    from amount_words import amount_in_words
    from app import current_lang
    from blueprints.invoicing import invoice_settings
    r = _validated_or_404(month)
    rows = transfer_lines(r)
    total = sum(x["net"] for x in rows)
    return render_template("pay_letter_print.html", run=r, label=_month_label(month), rows=rows,
                           account=_pay_account(r),
                           total=total, words=amount_in_words(total, current_lang()),
                           co=invoice_settings(), libelle=_libelle(month),
                           today=request.args.get("date") or date.today().isoformat())


@payroll_bp.route("/paie/<month>/virement-effectue", methods=["POST"])
@login_required
def run_transfer_done(month):
    """The bank has carried the transfers out: their total leaves the company
    account in Comptes on that day."""
    r = _validated_or_404(month)
    t = get_t()
    day = (request.form.get("transfer_on") or "").strip()
    if r.transfer_on:
        abort(403)
    acc = _pay_account(r)
    if acc is None:
        flash("error|" + t["run.err.account"])
    elif not _valid_day(day) or day < r.month + "-01":
        flash("error|" + t["run.err.transfer_day"])
    else:
        r.pay_account_id = acc.id
        r.transfer_on = day
        r.transfer_total = sum(x["net"] for x in transfer_lines(r))
        r.transfer_reference = (request.form.get("reference") or "").strip()[:60] or None
        r.transfer_by = current_user.id
        log_action("UPDATE", "pay_run", resource_id=r.id,
                   detail="Transfers of %s done on %s from '%s': %d GNF" % (month, day, acc.name, r.transfer_total))
        db.session.commit()
        flash("success|" + t["run.transfer_saved"])
    return redirect(url_for("payroll.run", month=month, tab="payment"))


@payroll_bp.route("/paie/<month>/virement-annuler", methods=["POST"])
@login_required
def run_transfer_undo(month):
    """Typed by mistake: the transfer is no longer recorded as done."""
    r = _validated_or_404(month)
    if not r.transfer_on:
        abort(404)
    r.transfer_on = r.transfer_total = r.transfer_reference = r.transfer_by = None
    log_action("UPDATE", "pay_run", resource_id=r.id, detail="Transfers of %s marked not done" % month)
    db.session.commit()
    flash("success|" + get_t()["run.transfer_undone"])
    return redirect(url_for("payroll.run", month=month, tab="payment"))
