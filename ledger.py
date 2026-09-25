"""The account a money line is coded to.

Caisse, Factures fournisseurs and Facturation each carry a code from the plan
on their lines, for the journal the comptable imports. The pickers offer the
accounts marked used on the plan and nothing else; the code starts empty and
is filled by hand, at entry or later.
"""
from models import AppSetting, LedgerAccount, db

# The till itself is not a purse on Comptes ("paid from the box" is the
# absence of one), so its account lives in a setting: 571 in practice.
TILL_KEY = "ledger.till_code"

# Where suppliers live on the plan: each permanent one under 4011 with his FP
# number, the occasional ones together on one sub-account.
SUPPLIER_PARENT = "4011"
SUNDRY_SUPPLIERS_CODE = "4011900"
SUNDRY_SUPPLIERS_LABEL = {"fr": "Fournisseurs divers", "en": "Sundry suppliers"}


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


def till_code():
    """The account of the plan the cash box is, or None until it is set."""
    row = db.session.get(AppSetting, TILL_KEY)
    return (row.value or None) if row else None


def set_till_code(code, user_id=None):
    row = db.session.get(AppSetting, TILL_KEY)
    if row is None:
        row = AppSetting(key=TILL_KEY, value=code or "", label="Compte de la caisse", category="finance")
        db.session.add(row)
    row.value = code or ""
    row.updated_by = user_id


def used_accounts_in(classes):
    """The shortlist narrowed to some classes, for a picker that only makes
    sense there: 4 and 5 for a purse, 4 for a supplier."""
    return [a for a in used_accounts() if a.klass in classes]


def _add_account(code, label, parent_code, user_id=None):
    row = LedgerAccount(code=code, label=label, klass=int(code[0]), parent_code=parent_code,
                        is_standard=False, is_used=True, is_active=True, created_by=user_id)
    db.session.add(row)
    db.session.flush()
    return row


def ensure_supplier_account(supplier, user_id=None):
    """The supplier's account on the plan, made on first need.

    A permanent supplier (FP-003) gets 4011003, labelled with his name; the
    occasional ones (FD) share 4011900 Fournisseurs divers. Set by hand on
    the supplier's form, it is left alone. Returns the code, or None when
    the plan is not loaded (no 4011 to hang it on)."""
    if supplier.ledger_code:
        return supplier.ledger_code
    parent = LedgerAccount.query.filter_by(code=SUPPLIER_PARENT).first()
    if not parent:
        return None
    if supplier.kind == "permanent" and supplier.code and "-" in supplier.code:
        number = supplier.code.split("-", 1)[1]
        code = SUPPLIER_PARENT + number.zfill(3)
        label = supplier.name
    else:
        code, label = SUNDRY_SUPPLIERS_CODE, SUNDRY_SUPPLIERS_LABEL["fr"]
    row = LedgerAccount.query.filter_by(code=code).first()
    if row:
        if not row.is_used:
            row.is_used = True
        if not row.is_active:
            row.is_active = True
    else:
        _add_account(code, label, parent.code, user_id)
    supplier.ledger_code = code
    return code


def labels_for(codes):
    """{code: label} for the codes given, for lists and detail pages."""
    codes = {c for c in codes if c}
    if not codes:
        return {}
    rows = LedgerAccount.query.filter(LedgerAccount.code.in_(codes)).all()
    return {r.code: r.label for r in rows}
