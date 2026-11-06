"""Pay, worked out for one employee and one month -- the payslip of a declared
employee, the line of anybody on the month's salary list.

Everything the law decides comes from the dated pay settings (PaySetting) in
force at the end of the month, and the pay from the salary line in force then
(Staff.salary_on): nothing here is a figure of its own, so a change of law or
a raise is a new dated line and the months before it come out the same.

The way the bases are taken follows the client's own payslip:
- CNSS on the gross salary of the items it is due on, held between the floor
  and the ceiling;
- RTS on the taxable gross less the employee's CNSS, by the scale's steps;
- versement forfaitaire and taxe d'apprentissage on the whole gross, paid by
  the employer.
Somebody not declared to the CNSS has none of these: his gross is his pay.

The advances the cash box handed out against the month (CashMovement "avance")
are taken back in full from the net; they change neither the CNSS nor the RTS.
A bonus the box paid for the month (CashMovement "prime") is pay: it counts in
the gross like an allowance of its kind (taxed, CNSS due, or not), then comes
off the net as already paid. The month's adjustments (PayRunAdjustment) add to
the gross, take off the gross, or take off the net.

compute() returns plain numbers, strings and lists only, so a validated
month keeps it as it is (PayRunLine.slip) and prints from it for ever.
"""
import calendar
import json
from decimal import ROUND_HALF_UP, Decimal

from models import CashMovement, PayAllowance, PayRun, PayRunLine, PaySetting, Staff


def _pct(amount, rate):
    """amount x rate % rounded to the franc."""
    return int((Decimal(amount) * Decimal(rate) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rts_on(base, brackets):
    """The RTS of a monthly taxable amount on a scale (steps sorted by lower
    bound): each slice taxed at its own step's rate."""
    total = Decimal(0)
    steps = list(brackets)
    for i, step in enumerate(steps):
        upper = steps[i + 1].lower if i + 1 < len(steps) else None
        if base <= step.lower:
            break
        top = base if upper is None else min(base, upper)
        total += Decimal(top - step.lower) * Decimal(step.rate) / 100
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def advances_for(person, year, month, kind="avance"):
    """The advances to take back from this month's salary -- or, with kind
    "prime", the bonuses the cash box paid for it."""
    return (CashMovement.query
            .filter_by(kind=kind, staff_id=person.id, advance_month="%04d-%02d" % (year, month))
            .order_by(CashMovement.date, CashMovement.id).all())


def month_bounds(year, month):
    last = calendar.monthrange(year, month)[1]
    return "%04d-%02d-01" % (year, month), "%04d-%02d-%02d" % (year, month, last)


def _who(person, contract):
    """The person as the payslip prints him, kept with the month."""
    return dict(
        id=person.id, name=person.name, matricule=person.matricule,
        position=person.position.name if person.position else None,
        category=person.category,
        department=person.department.name if person.department else None,
        site=person.site.name if person.site else None,
        cnss_number=person.cnss_number, hired_on=person.hired_on,
        marital_status=person.marital_status, children=person.children,
        nationality=person.nationality, declared=bool(person.declared),
        pay_method=person.pay_method,
        bank=person.bank.name if person.bank else None,
        bank_code=person.bank.code if person.bank else None,
        bank_branch=person.bank_branch, bank_account=person.bank_account,
        bank_key=person.bank_key, mobile_money=person.mobile_money or person.phone,
        contract_kind=contract.kind if contract else None,
        contract_start=contract.start_date if contract else None,
    )


def compute(person, year, month, adjustments=()):
    """(slip, None) or (None, error key) -- one employee's pay for a month.
    `adjustments` are the month's PayRunAdjustment rows for him."""
    first, last = month_bounds(year, month)
    salary = person.salary_on(last)
    if salary is None:
        return None, "payslip.err.no_salary"
    declared = bool(person.declared)
    setting = PaySetting.on(last) if declared else None
    if declared and setting is None:
        return None, "payslip.err.no_setting"

    # What is paid on top of the base: the month's allowances from the salary
    # line, the bonuses the cash box paid for the month, the adjustments.
    gains = [dict(name=a.allowance.name, amount=a.amount, taxable=a.allowance.taxable,
                  cnss=a.allowance.cnss, code=a.allowance.ledger_code, kind="allowance")
             for a in sorted((a for a in salary.allowances if a.amount),
                             key=lambda a: (a.allowance.sort_order, a.allowance.name))]
    bonuses = advances_for(person, year, month, "prime")
    for b in bonuses:
        item = b.pay_item
        gains.append(dict(name=item.name if item else "Prime", amount=b.amount,
                          taxable=bool(item and item.taxable), cnss=bool(item and item.cnss),
                          code=item.ledger_code if item else None, kind="bonus"))
    for a in adjustments:
        if a.kind in ("plus", "minus"):
            gains.append(dict(name=a.label, amount=a.amount if a.kind == "plus" else -a.amount,
                              taxable=a.taxable, cnss=a.cnss, code=None, kind="adjustment"))
    base = salary.amount
    gross_taxable = base + sum(g["amount"] for g in gains if g["taxable"])
    gross = gross_taxable + sum(g["amount"] for g in gains if not g["taxable"])

    cnss_base = cnss_employee = cnss_employer = rts_base = rts = vf = ta = 0
    rates = None
    if declared:
        cnss_base = base + sum(g["amount"] for g in gains if g["cnss"])
        if setting.cnss_ceiling:
            cnss_base = min(cnss_base, setting.cnss_ceiling)
        if setting.cnss_floor:
            cnss_base = max(cnss_base, setting.cnss_floor)
        cnss_employee = _pct(cnss_base, setting.cnss_employee_rate)
        cnss_employer = _pct(cnss_base, setting.cnss_employer_rate)
        rts_base = max(gross_taxable - cnss_employee, 0)
        rts = rts_on(rts_base, setting.brackets)
        vf = _pct(gross, setting.vf_rate)
        ta = _pct(gross, setting.ta_rate)
        rates = dict(effective_on=setting.effective_on,
                     cnss_employee_rate=str(setting.cnss_employee_rate),
                     cnss_employer_rate=str(setting.cnss_employer_rate),
                     vf_rate=str(setting.vf_rate), ta_rate=str(setting.ta_rate))

    advances = [dict(date=a.date, amount=a.amount) for a in advances_for(person, year, month)]
    prepaid = [dict(name=b.pay_item.name if b.pay_item else "Prime", date=b.date, amount=b.amount)
               for b in bonuses]
    other = [dict(label=a.label, amount=a.amount) for a in adjustments if a.kind == "net"]
    advances_total = sum(a["amount"] for a in advances)
    prepaid_total = sum(b["amount"] for b in prepaid)
    other_total = sum(o["amount"] for o in other)
    withheld = cnss_employee + rts + advances_total + prepaid_total + other_total
    employer = cnss_employer + vf + ta
    contract = _contract_on(person, last)
    return dict(
        year=year, month=month, first=first, last=last,
        who=_who(person, contract), declared=declared, setting=rates,
        base=base, gains=gains,
        taxable_allowances=[g for g in gains if g["taxable"]],
        free_allowances=[g for g in gains if not g["taxable"]],
        gross_taxable=gross_taxable, gross=gross,
        cnss_base=cnss_base, cnss_employee=cnss_employee, cnss_employer=cnss_employer,
        rts_base=rts_base, rts=rts, vf=vf, ta=ta,
        advances=advances, advances_total=advances_total,
        bonuses=prepaid, prepaid_total=prepaid_total,
        other=other, other_total=other_total,
        withheld=withheld, employer=employer,
        net=gross - withheld, cost=gross + employer,
    ), None


def payslip(person, year, month):
    """The payslip of a declared employee: the frozen one once the month is
    validated, else worked out now with the month's adjustments."""
    line = frozen_line(person, year, month)
    if line is not None:
        if not line.declared:
            return None, "payslip.err.not_declared"
        return json.loads(line.slip), None
    if not person.is_employee or not person.declared:
        return None, "payslip.err.not_declared"
    run = PayRun.query.filter_by(month="%04d-%02d" % (year, month)).first()
    adjustments = [a for a in run.adjustments if a.staff_id == person.id] if run else ()
    return compute(person, year, month, adjustments)


def frozen_line(person, year, month):
    return (PayRunLine.query.join(PayRun)
            .filter(PayRun.month == "%04d-%02d" % (year, month), PayRun.status == "validated",
                    PayRunLine.staff_id == person.id).first())


def _contract_on(person, day):
    """The contract the person was under on that day, if any."""
    for c in person.contracts:          # newest first
        if c.start_date <= day and (not c.ended_on or c.ended_on >= day):
            return c
    return None


def paid_this_month(person, first, last):
    """Whether an employee is on the month's salary list: a salary line in
    force at the end of the month, and -- for somebody with contracts -- one
    running at some point in the month."""
    if not person.is_employee or person.salary_on(last) is None:
        return False
    if not person.contracts:
        return True
    return any(c.start_date <= last and not (c.ended_on and c.ended_on < first)
               and not (c.end_date and not c.ended_on and c.end_date < first)
               for c in person.contracts)


def run_lines(run):
    """[(person, slip, error)] for every employee of the month, by department
    then name. A validated month reads its frozen lines; a draft is worked
    out now."""
    year, month = int(run.month[:4]), int(run.month[5:])
    if run.status == "validated":
        out = [(l.person, json.loads(l.slip), None) for l in run.lines]
    else:
        first, last = month_bounds(year, month)
        by_person = {}
        for a in run.adjustments:
            by_person.setdefault(a.staff_id, []).append(a)
        people = Staff.query.filter(Staff.is_active.is_(True), Staff.kind == "employee").all()
        # Somebody with an adjustment stays on the list even if archived since.
        extra = {a.staff_id for a in run.adjustments} - {p.id for p in people}
        if extra:
            people += Staff.query.filter(Staff.id.in_(extra)).all()
        out = []
        for p in people:
            if not paid_this_month(p, first, last) and p.id not in by_person:
                continue
            slip, err = compute(p, year, month, by_person.get(p.id, ()))
            out.append((p, slip, err))
    out.sort(key=lambda r: ((r[1] or {}).get("who", {}).get("department") or "￿", r[0].name))
    return out


def freeze(run):
    """Validating the month: each employee's figures written down for good."""
    run.lines = []
    for person, slip, err in run_lines(run):
        if slip is None:
            continue
        run.lines.append(PayRunLine(
            staff_id=person.id, declared=slip["declared"], pay_method=slip["who"]["pay_method"],
            gross=slip["gross"], cnss_employee=slip["cnss_employee"], cnss_employer=slip["cnss_employer"],
            rts=slip["rts"], vf=slip["vf"], ta=slip["ta"], advances=slip["advances_total"],
            prepaid=slip["prepaid_total"], other=slip["other_total"], net=slip["net"],
            cost=slip["cost"], slip=json.dumps(slip)))


def active_allowances(kind="allowance"):
    """The monthly allowances in use -- or, with kind "bonus", the bonuses."""
    return (PayAllowance.query.filter_by(is_active=True, kind=kind)
            .order_by(PayAllowance.sort_order, PayAllowance.name).all())


# ── The month in the books ───────────────────────────────────────────────────
#
# A validated month as the comptable writes it, SYSCOHADA: each charge owed to
# the staff (422), then out of what is owed, what goes to the CNSS (431), to
# the State (4472 the RTS) and what settles their advances (4211) and the
# bonuses the cash box paid ahead (4212); the employer's own charges beside.
# The net left in 422 is cleared when the bank or the cash box pays it.

PAY_CODES = {
    "base": "6611",            # appointements, salaires
    "allowance": "6638",       # autres indemnités (when the item names none)
    "bonus": "6612",           # primes et gratifications (idem)
    "adjustment": "6611",
    "owed": "422",             # personnel, rémunérations dues
    "cnss": "431",             # sécurité sociale
    "rts": "4472",             # impôts sur salaires
    "advance": "4211",         # personnel, avances
    "prepaid": "4212",         # personnel, acomptes
    "cnss_employer": "6641",   # charges sociales
    "payroll_taxes": "6413",   # taxes sur appointements et salaires (VF, TA)
    "payroll_taxes_due": "4478",
}


def journal_entries(run):
    """[(debit, credit, amount, what)] -- the month's pay summed by account.
    `what` names the line: "gross", "cnss", "rts", "advance", "prepaid",
    "other", "cnss_employer", "payroll_taxes"."""
    charges = {}
    totals = dict(cnss=0, rts=0, advance=0, prepaid=0, other=0, cnss_employer=0, payroll_taxes=0)
    for line in run.lines:
        s = json.loads(line.slip)
        charges[PAY_CODES["base"]] = charges.get(PAY_CODES["base"], 0) + s["base"]
        for g in s["gains"]:
            code = g.get("code") or PAY_CODES[g.get("kind") or "allowance"]
            charges[code] = charges.get(code, 0) + g["amount"]
        totals["cnss"] += s["cnss_employee"] + s["cnss_employer"]
        totals["cnss_employer"] += s["cnss_employer"]
        totals["rts"] += s["rts"]
        totals["advance"] += s["advances_total"]
        totals["prepaid"] += s["prepaid_total"]
        totals["other"] += s["other_total"]
        totals["payroll_taxes"] += s["vf"] + s["ta"]
    owed = PAY_CODES["owed"]
    out = [(code, owed, amount, "gross") for code, amount in sorted(charges.items()) if amount]
    employee_cnss = totals["cnss"] - totals["cnss_employer"]
    out += [(owed, PAY_CODES["cnss"], employee_cnss, "cnss"),
            (owed, PAY_CODES["rts"], totals["rts"], "rts"),
            (owed, PAY_CODES["advance"], totals["advance"], "advance"),
            (owed, PAY_CODES["prepaid"], totals["prepaid"], "prepaid"),
            # What else came off the net -- a refund owed, a fine -- depends
            # on what it was: left for the comptable to code, never guessed.
            (owed, None, totals["other"], "other"),
            (PAY_CODES["cnss_employer"], PAY_CODES["cnss"], totals["cnss_employer"], "cnss_employer"),
            (PAY_CODES["payroll_taxes"], PAY_CODES["payroll_taxes_due"], totals["payroll_taxes"], "payroll_taxes")]
    return [e for e in out if e[2]]


def transfer_lines(run):
    """The validated month's employees paid by bank transfer, with the parts
    of their account the bank's file asks for."""
    out = []
    for line in run.lines:
        if line.pay_method != "transfer":
            continue
        s = json.loads(line.slip)
        out.append(dict(person=line.person, who=s["who"], net=line.net))
    return sorted(out, key=lambda r: r["who"]["name"])


def box_lines(run):
    """The validated month's employees the cash box pays (Orange Money, cash),
    each with the movement that paid him, if any."""
    paid = {m.staff_id: m for m in CashMovement.query.filter_by(kind="salaire", advance_month=run.month).all()}
    out = []
    for line in run.lines:
        if line.pay_method not in ("mobile", "cash"):
            continue
        s = json.loads(line.slip)
        out.append(dict(line=line, person=line.person, who=s["who"], net=line.net,
                        paid=paid.get(line.staff_id)))
    return sorted(out, key=lambda r: r["who"]["name"])
