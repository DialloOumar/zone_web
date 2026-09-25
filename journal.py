"""The journal: every money line the app holds, with the two accounts of
the plan it moves, in the four books the comptable keeps.

    caisse   what the box paid or received: a cost paid in cash or by a
             purse that is owed back, money in and out of the box
    banque   what left a bank account: a bill's instalment, a bank charge
    achats   the supplier bills, as received
    ventes   the client invoices, as issued, and the receipts on them

Nothing is written here. Each line is read from where it was entered and
its two sides are worked out from what is already attached: the code on
the line, the supplier's or the client's account, the purse's account,
the till's. A line missing a side is shown as such and offered for coding
where the code is the line's own to set.

The export is the same lines, one row per side, in the flat form the
accounting packages import: date, journal, pièce, compte, libellé, débit,
crédit.
"""
import csv
import io

from ledger import labels_for, till_code
from models import (BankCharge, CashMovement, ClientInvoice, ClientInvoiceLine, ClientPayment,
                    Expense, SupplierInvoice, SupplierPayment, db)

JOURNALS = ("caisse", "banque", "achats", "ventes")

# Which sources may have their code set from the journal page: the line's
# own charge or revenue, never a settlement's (that one is the tiers').
CODABLE = {"expense", "supplier_invoice", "bank_charge", "client_invoice_line"}


def _line(journal, kind, row, date, label, amount, debit, credit, piece=None,
          who=None, codable=False, code_side=None, method=None, reference=None):
    return dict(journal=journal, kind=kind, id=row.id, date=date, label=label or "",
                amount=int(amount or 0), debit=debit, credit=credit, piece=piece or "",
                who=who or "", codable=codable, code_side=code_side,
                method=method or "", reference=reference or "",
                complete=bool(debit and credit))


def _purse_code(account):
    return account.ledger_code if account else None


def lines(date_from=None, date_to=None, journal=None):
    """The journal's lines in the period, newest first."""
    till = till_code()
    out = []

    def within(q, col):
        if date_from:
            q = q.filter(col >= date_from)
        if date_to:
            q = q.filter(col <= date_to)
        return q

    if journal in (None, "caisse"):
        # A cost: its charge (or the supplier's account when it settles a
        # bill) against the till, or against the purse that advanced it.
        for x in within(Expense.query, Expense.date).all():
            settles = x.supplier_payment is not None
            who = x.supplier_payment.invoice.supplier.name if settles and x.supplier_payment.invoice else (x.staff.name if x.staff else None)
            out.append(_line(
                "caisse", "expense", x, x.date,
                x.description or x.label or x.category,
                x.amount, debit=x.ledger_code, credit=_purse_code(x.account) if x.account_id else till,
                piece=(x.supplier_payment.invoice.number if settles and x.supplier_payment.invoice else None),
                who=who, codable=not settles, code_side="debit",
                method=x.payment_method, reference=x.payment_reference))
        # Money in and out of the box, against the purse. The money-in the
        # app wrote for a cost a purse paid is not a movement of its own:
        # that money is the cost above.
        for m in within(CashMovement.query.filter(CashMovement.expense_id.is_(None)), CashMovement.date).all():
            purse = _purse_code(m.account)
            if m.kind == "depot":
                out.append(_line("caisse", "cash_movement", m, m.date, m.note or m.source or "Entrée en caisse",
                                 m.amount, debit=till, credit=purse, who=m.account.name if m.account else None,
                                 method=m.method, reference=m.reference))
            else:
                out.append(_line("caisse", "cash_movement", m, m.date, m.note or m.source or "Retrait de caisse",
                                 m.amount, debit=purse, credit=till, who=m.account.name if m.account else None,
                                 method=m.method, reference=m.reference))

    if journal in (None, "banque"):
        # A bill's instalment from a bank account: the supplier's account
        # against the bank's.
        for p in within(SupplierPayment.query.filter(SupplierPayment.expense_id.is_(None)), SupplierPayment.date).all():
            inv = p.invoice
            out.append(_line("banque", "supplier_payment", p, p.date,
                             "Règlement facture" + (" " + inv.number if inv and inv.number else ""),
                             p.amount, debit=p.ledger_code, credit=_purse_code(p.account),
                             piece=inv.number if inv else None, who=inv.supplier.name if inv and inv.supplier else None,
                             method=p.method, reference=p.reference))
        for c in within(BankCharge.query, BankCharge.date).all():
            out.append(_line("banque", "bank_charge", c, c.date, c.description or c.payee, c.amount,
                             debit=c.ledger_code, credit=_purse_code(c.account),
                             who=c.payee, codable=True, code_side="debit",
                             method=c.method, reference=c.reference))

    if journal in (None, "achats"):
        # A bill as received: the charge against the supplier's account.
        for inv in within(SupplierInvoice.query, SupplierInvoice.date).all():
            out.append(_line("achats", "supplier_invoice", inv, inv.date, inv.description or "Facture fournisseur",
                             inv.amount, debit=inv.ledger_code,
                             credit=inv.supplier.ledger_code if inv.supplier else None,
                             piece=inv.number, who=inv.supplier.name if inv.supplier else None,
                             codable=True, code_side="debit"))

    if journal in (None, "ventes"):
        # An invoice as issued, one line per line: the client's account
        # against the revenue. A cancelled invoice is not a sale.
        q = within(ClientInvoice.query.filter(ClientInvoice.status != "cancelled"), ClientInvoice.date)
        for inv in q.all():
            client_code = inv.client.ledger_code if inv.client else None
            for l in inv.lines:
                out.append(_line("ventes", "client_invoice_line", l, inv.date,
                                 "%s %s" % (l.vehicle_code or "", l.category_label or ""),
                                 l.amount, debit=client_code, credit=l.ledger_code,
                                 piece=inv.number, who=inv.client.name if inv.client else None,
                                 codable=True, code_side="credit"))
        # A receipt: the purse (or the till) against the client's account.
        for p in within(ClientPayment.query, ClientPayment.date).all():
            inv = p.invoice
            out.append(_line("ventes", "client_payment", p, p.date,
                             "Encaissement" + (" " + inv.number if inv and inv.number else ""),
                             p.amount, debit=_purse_code(p.account) if p.account_id else till,
                             credit=p.ledger_code, piece=inv.number if inv else None,
                             who=inv.client.name if inv and inv.client else None,
                             method=p.method, reference=p.reference))

    out.sort(key=lambda r: (r["date"], r["journal"], r["id"]), reverse=True)
    return out


def with_labels(rows):
    """The labels of every account the rows name, for display."""
    codes = set()
    for r in rows:
        codes.add(r["debit"])
        codes.add(r["credit"])
    return labels_for(codes)


def set_code(kind, row_id, code):
    """Code a line from the journal page. Returns the row, or None when the
    kind is not one whose code is set here or the row is gone."""
    model = {"expense": Expense, "supplier_invoice": SupplierInvoice,
             "bank_charge": BankCharge, "client_invoice_line": ClientInvoiceLine}.get(kind)
    if not model:
        return None
    row = db.session.get(model, row_id)
    if row is None:
        return None
    if kind == "expense" and row.supplier_payment is not None:
        return None   # a settlement's code is the supplier's, not ours to set
    row.ledger_code = code
    return row


def to_csv(rows, labels):
    """One row per side, the flat form accounting packages import:
    date;journal;pièce;compte;intitulé;libellé;tiers;mode;référence;débit;crédit"""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["date", "journal", "piece", "compte", "intitule", "libelle", "tiers", "mode", "reference", "debit", "credit"])
    for r in sorted(rows, key=lambda r: (r["date"], r["journal"], r["id"])):
        base = [r["date"], r["journal"].upper(), r["piece"]]
        tail = [r["who"], r["method"], r["reference"]]
        w.writerow(base + [r["debit"] or "", labels.get(r["debit"], ""), r["label"]] + tail + [r["amount"], ""])
        w.writerow(base + [r["credit"] or "", labels.get(r["credit"], ""), r["label"]] + tail + ["", r["amount"]])
    return buf.getvalue()
