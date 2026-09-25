"""The account a money line is coded to.

Caisse, Factures fournisseurs and Facturation each carry a code from the plan
on their lines, for the journal the comptable imports. The pickers offer the
accounts marked used on the plan and nothing else; the code starts empty and
is filled by hand, at entry or later.
"""
from models import AppSetting, LedgerAccount, db

# The till itself is not a purse on Comptes ("paid from the box" is the
# absence of one), so its account lives in a setting: 5711, cash in local
# currency, unless someone picks another.
TILL_KEY = "ledger.till_code"
TILL_DEFAULT = "5711"

# Where purses live: a bank (or a mobile-money line) under 521, on one of
# the plan's placeholder accounts (5211 "Banque X") relabelled with its name
# while any is free, else on the next code; a purse that is owed back (the
# boss's own money) under 4621, the partners' current accounts.
BANK_PARENT = "521"
PARTNER_PARENT = "4621"

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


def _mark_used(row):
    row.is_used = True
    row.is_active = True
    return row


def ensure_till_code(user_id=None):
    """The till's account, 5711 by default, set once when none is."""
    if till_code():
        return till_code()
    row = LedgerAccount.query.filter_by(code=TILL_DEFAULT).first()
    if not row:
        return None
    _mark_used(row)
    set_till_code(row.code, user_id)
    return row.code


def ensure_purse_account(purse, user_id=None):
    """The purse's account on the plan, made on first need and left alone
    once set by hand. Returns the code, or None when the plan is not
    loaded."""
    if purse.ledger_code:
        return purse.ledger_code
    from models import CashAccount
    parent_code = PARTNER_PARENT if purse.is_repayable else BANK_PARENT
    parent = LedgerAccount.query.filter_by(code=parent_code).first()
    if not parent:
        return None
    taken = {c for (c,) in db.session.query(CashAccount.ledger_code)
             .filter(CashAccount.ledger_code.isnot(None)).all()}
    row = None
    if not purse.is_repayable:
        # A placeholder the plan ships with, still free: it becomes this bank.
        for cand in (LedgerAccount.query
                     .filter(LedgerAccount.parent_code == parent.code,
                             LedgerAccount.is_standard.is_(True))
                     .order_by(LedgerAccount.code).all()):
            if cand.code not in taken and not cand.is_used and cand.label.lower().startswith("banque "):
                cand.label = purse.name
                row = _mark_used(cand)
                break
    if row is None:
        existing = {a.code for a in LedgerAccount.query
                    .filter(LedgerAccount.code.like(parent.code + "%")).all()}
        n = 1
        while parent.code + str(n) in existing or parent.code + str(n) in taken:
            n += 1
        row = _add_account(parent.code + str(n), purse.name, parent.code, user_id)
    purse.ledger_code = row.code
    return row.code


def attach_existing_purses():
    """Catch-up for the purses from before the plan, and the till's own
    account. Safe to run again: anything set by hand is left alone."""
    from models import CashAccount
    if not LedgerAccount.query.filter_by(code=BANK_PARENT).first():
        return 0
    n = 0
    for p in CashAccount.query.order_by(CashAccount.sort_order, CashAccount.name).all():
        if not p.ledger_code and ensure_purse_account(p):
            n += 1
    ensure_till_code()
    db.session.commit()
    return n


def attach_existing_suppliers():
    """One-time catch-up, safe to run again: every supplier from before the
    plan gets his account (a permanent one his own after his FP code, an
    occasional one the shared 4011900), and every settlement already
    recorded on his bills is coded to it. Set by hand since, a supplier's
    account is left alone; a settlement already coded is left alone.
    Returns (suppliers attached, settlements coded)."""
    from models import Supplier, SupplierInvoice, SupplierPayment, Expense
    if not LedgerAccount.query.filter_by(code=SUPPLIER_PARENT).first():
        return 0, 0
    attached = 0
    for s in Supplier.query.all():
        if not s.ledger_code and ensure_supplier_account(s):
            attached += 1
    coded = 0
    rows = (db.session.query(SupplierPayment, Supplier.ledger_code)
            .join(SupplierInvoice, SupplierPayment.invoice_id == SupplierInvoice.id)
            .join(Supplier, SupplierInvoice.supplier_id == Supplier.id)
            .filter(SupplierPayment.ledger_code.is_(None), Supplier.ledger_code.isnot(None)).all())
    for pay, code in rows:
        pay.ledger_code = code
        # The cash box's instalment mirrors a cost: same code on the cost.
        if pay.expense_id:
            x = db.session.get(Expense, pay.expense_id)
            if x is not None and not x.ledger_code:
                x.ledger_code = code
        coded += 1
    db.session.commit()
    return attached, coded


def labels_for(codes):
    """{code: label} for the codes given, for lists and detail pages."""
    codes = {c for c in codes if c}
    if not codes:
        return {}
    rows = LedgerAccount.query.filter(LedgerAccount.code.in_(codes)).all()
    return {r.code: r.label for r in rows}
