"""A payslip, worked out for one declared employee and one month.

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
"""
import calendar
from decimal import ROUND_HALF_UP, Decimal

from models import PayAllowance, PaySetting


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


def month_bounds(year, month):
    last = calendar.monthrange(year, month)[1]
    return "%04d-%02d-01" % (year, month), "%04d-%02d-%02d" % (year, month, last)


def payslip(person, year, month):
    """(slip, None) or (None, error key). The slip holds every line and total
    the printed payslip shows."""
    first, last = month_bounds(year, month)
    if not person.is_employee or not person.declared:
        return None, "payslip.err.not_declared"
    salary = person.salary_on(last)
    if salary is None:
        return None, "payslip.err.no_salary"
    setting = PaySetting.on(last)
    if setting is None:
        return None, "payslip.err.no_setting"

    allowances = sorted((a for a in salary.allowances if a.amount),
                        key=lambda a: (a.allowance.sort_order, a.allowance.name))
    base = salary.amount
    taxable_allow = sum(a.amount for a in allowances if a.allowance.taxable)
    free_allow = sum(a.amount for a in allowances if not a.allowance.taxable)
    gross_taxable = base + taxable_allow
    gross = gross_taxable + free_allow

    cnss_gross = base + sum(a.amount for a in allowances if a.allowance.cnss)
    cnss_base = cnss_gross
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

    withheld = cnss_employee + rts
    employer = cnss_employer + vf + ta
    return dict(
        person=person, year=year, month=month, first=first, last=last,
        salary=salary, setting=setting, contract=_contract_on(person, last),
        base=base, allowances=allowances,
        taxable_allowances=[a for a in allowances if a.allowance.taxable],
        free_allowances=[a for a in allowances if not a.allowance.taxable],
        gross_taxable=gross_taxable, gross=gross,
        cnss_base=cnss_base, cnss_employee=cnss_employee, cnss_employer=cnss_employer,
        rts_base=rts_base, rts=rts, vf=vf, ta=ta,
        withheld=withheld, employer=employer,
        net=gross - withheld, cost=gross + employer,
    ), None


def _contract_on(person, day):
    """The contract the person was under on that day, if any."""
    for c in person.contracts:          # newest first
        if c.start_date <= day and (not c.ended_on or c.ended_on >= day):
            return c
    return None


def active_allowances():
    return (PayAllowance.query.filter_by(is_active=True)
            .order_by(PayAllowance.sort_order, PayAllowance.name).all())
