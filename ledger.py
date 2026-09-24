"""The account a money line is coded to.

Caisse, Factures fournisseurs and Facturation each carry a code from the plan
on their lines, for the journal the comptable imports. The pickers offer the
accounts marked used on the plan and nothing else; the code starts empty and
is filled by hand, at entry or later.
"""
from models import LedgerAccount, db


def used_accounts():
    """The shortlist, in chart order: active accounts marked used."""
    return (LedgerAccount.query
            .filter(LedgerAccount.is_active.is_(True), LedgerAccount.is_used.is_(True))
            .order_by(LedgerAccount.code).all())


def read_code(form, field="ledger_code"):
    """The code a form sent, or None. Returns (code, ok): a code that is not
    on the shortlist is refused, an empty one is fine."""
    code = (form.get(field) or "").strip()
    if not code:
        return None, True
    row = (LedgerAccount.query
           .filter_by(code=code, is_active=True, is_used=True).first())
    return (row.code, True) if row else (None, False)


def labels_for(codes):
    """{code: label} for the codes given, for lists and detail pages."""
    codes = {c for c in codes if c}
    if not codes:
        return {}
    rows = LedgerAccount.query.filter(LedgerAccount.code.in_(codes)).all()
    return {r.code: r.label for r in rows}
