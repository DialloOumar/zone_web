"""Comptes — the company's purses, and the people who front it money.

The boss's own account, the company's bank account, the Orange Money line, an
agent who advances cash when the till is empty. One list for the whole app:
the account that tops up the cash box is the same account that wires a
supplier's balance, so it is named once and used from both places.

It used to live inside the cash box's "Sites et comptes" page, behind the
permission to spend from the box. Accounting settles bills from these accounts
without ever touching the box, so the list gets a page of its own, open to
whoever may spend from the box or record a bill.
"""
from datetime import date

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from app import get_t, is_modal_request, log_action, modal_ok, require_any_perm, require_perm
from blueprints.expenses import _save_list_row
from blueprints.supplier_invoices import _amount, _apply_photo_change, _valid_date
from ledger import ensure_till_code, labels_for, read_code, set_till_code, till_code, used_accounts_in
from models import (AccountDeposit, BankCharge, CashAccount, CashMovement, CashTransfer, ClientPayment,
                    Expense, SupplierPayment, db)

accounts_bp = Blueprint("accounts", __name__)

# Who may keep the list: the cashier, whoever records supplier bills, and
# whoever records what clients pay.
# Who opens Comptes: whoever spends from the box, records a bill or an
# invoice -- and whoever keeps the accounts, for the purses' and the till's
# accounts on the plan.
MANAGE = ("expense.create", "supplier_invoice.create", "invoicing.manage",
          "accounting.view", "accounting.manage")


def _used_ids():
    """Accounts something points at, which may be archived but never deleted:
    a money-in or money-out of the box, a cost an account paid directly, or an
    instalment on a supplier's bill."""
    used = set()
    for model, col in ((CashMovement, CashMovement.account_id),
                       (Expense, Expense.account_id),
                       (SupplierPayment, SupplierPayment.account_id),
                       (BankCharge, BankCharge.account_id),
                       (ClientPayment, ClientPayment.account_id),
                       (AccountDeposit, AccountDeposit.account_id),
                       (CashTransfer, CashTransfer.account_id)):
        used.update(r[0] for r in db.session.query(col)
                    .filter(col.isnot(None)).distinct().all())
    return used


def _paid_to_suppliers():
    """Per account: what has gone out of it on the Banque page -- bills'
    instalments paid from it, and charges paid straight from it. What the
    cash box paid is not here -- that money is the box's story, on Caisse."""
    rows = (db.session.query(SupplierPayment.account_id,
                             db.func.count(SupplierPayment.id),
                             db.func.coalesce(db.func.sum(SupplierPayment.amount), 0))
            .filter(SupplierPayment.account_id.isnot(None),
                    SupplierPayment.expense_id.is_(None))
            .group_by(SupplierPayment.account_id).all())
    out = {r[0]: {"count": int(r[1]), "total": int(r[2] or 0)} for r in rows}
    # ...and the charges paid straight from an account, with no bill behind.
    for acc_id, n, total in (db.session.query(BankCharge.account_id, db.func.count(BankCharge.id),
                                              db.func.coalesce(db.func.sum(BankCharge.amount), 0))
                             .group_by(BankCharge.account_id).all()):
        e = out.setdefault(acc_id, {"count": 0, "total": 0})
        e["count"] += int(n); e["total"] += int(total or 0)
    return out


@accounts_bp.route("/comptes")
@login_required
@require_any_perm(*MANAGE)
def index():
    # The till's account, 5711 unless someone chose otherwise: set here on
    # first visit so the card always has something to show.
    if ensure_till_code(current_user.id):
        db.session.commit()
    accounts = CashAccount.query.order_by(CashAccount.sort_order,
                                          CashAccount.name).all()
    return render_template(
        "accounts.html", accounts=accounts,
        balances={a.id: balance(a) for a in accounts if not a.is_repayable},
        used=_used_ids(), paid=_paid_to_suppliers(),
        till=till_code(), till_accounts=used_accounts_in((5,)),
        code_labels=labels_for([a.ledger_code for a in CashAccount.query.all()] + [till_code()]))


@accounts_bp.route("/comptes/caisse", methods=["POST"])
@login_required
@require_perm("accounting.manage")
def till():
    """The cash box's own account on the plan: the counterpart of every
    cost paid in cash, for the journal de caisse."""
    t = get_t()
    code, ok = read_code(request.form)
    if not ok or (code and code[0] != "5"):
        flash("error|" + t["accounts.err.till_class"])
    else:
        set_till_code(code, current_user.id)
        log_action("UPDATE", "setting", detail="Till account set to %s" % (code or "none"))
        db.session.commit()
        flash("success|" + t["accounts.till_saved"])
    return redirect(url_for("accounts.index"))


def _render_form(row, error=None):
    tpl = "_account_form.html" if is_modal_request() else "account_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error,
                           ledger_accounts=used_accounts_in((4, 5))), status


@accounts_bp.route("/comptes/nouveau", methods=["GET", "POST"])
@login_required
@require_any_perm(*MANAGE)
def new():
    if request.method == "POST":
        error = _save_list_row(CashAccount, None, "account")
        if error:
            return _render_form(None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.index"))
    return _render_form(None)


@accounts_bp.route("/comptes/<int:aid>/modifier", methods=["GET", "POST"])
@login_required
@require_any_perm(*MANAGE)
def edit(aid):
    row = db.session.get(CashAccount, aid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_list_row(CashAccount, row, "account")
        if error:
            return _render_form(row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.index"))
    return _render_form(row)


@accounts_bp.route("/comptes/<int:aid>/<any(archive,reactivate,delete):what>",
                   methods=["POST"])
@login_required
@require_any_perm(*MANAGE)
def action(aid, what):
    """Archive takes it out of the pickers and leaves its history named; delete
    is only for one nothing has ever pointed at."""
    row = db.session.get(CashAccount, aid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if row.id in _used_ids():
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(url_for("accounts.index"))
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "cash_account", resource_id=aid,
                   detail="Deleted account '%s'" % name)
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "cash_account", resource_id=aid,
                   detail="%s account '%s'" % (what.title(), row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(url_for("accounts.index"))


# ── What a company account holds ─────────────────────────────────────────────

# Whoever keeps Facturation puts money on the accounts: client payments land
# there already, and the rest of what comes in is theirs to record too.
FUND = "invoicing.manage"

DEPOSIT_METHODS = ("transfer", "cheque", "cash", "mobile_money", "other")


def deposit_accounts():
    """What money put on an account may be coded to: the capital and the
    loans (class 1), a partner's or another third party's money (class 4),
    an income with no client bill behind it (class 7)."""
    return used_accounts_in((1, 4, 7))


def account_lines(acc):
    """Everything that moved money on one company account since its starting
    balance, oldest first, each with the balance after it.

    In: the money put on it here, a client's payment received on it, money
    the cash box gave back to it. Out: money sent to the cash box, a supplier
    bill's instalment wired from it, a charge paid straight from it. A bill
    the cash box settled is not here: that money left the box, and if it came
    from this account, it did so as money given to the box."""
    since = acc.opening_date

    def q(model, *crit):
        query = model.query.filter(model.account_id == acc.id, *crit)
        if since:
            query = query.filter(model.date >= since)
        return query.all()

    rows = []
    for d in q(AccountDeposit):
        rows.append(dict(kind="deposit", row=d, date=d.date, label=d.description or "",
                         who=d.source, reference=d.reference, amount_in=d.amount, amount_out=0))
    for p in q(ClientPayment):
        inv = p.invoice
        rows.append(dict(kind="client_payment", row=p, date=p.date,
                         label=inv.number if inv else "",
                         who=inv.client.name if inv and inv.client else "",
                         reference=p.reference, amount_in=p.amount, amount_out=0))
    # Money sent to the cash box leaves the account the day it is sent,
    # whether the box has confirmed it yet or not; the box's own money-in for
    # it is the same money, so it is not counted a second time below.
    for x in q(CashTransfer, CashTransfer.status != "refused"):
        rows.append(dict(kind="transfer", row=x, date=x.date, label=x.note or "",
                         who=x.sender.full_name if x.sender else "", reference=x.reference,
                         amount_in=0, amount_out=x.amount))
    for m in q(CashMovement, CashMovement.transfer_id.is_(None)):
        given_back = m.kind == "retrait"
        rows.append(dict(kind="till_out" if given_back else "till_in", row=m, date=m.date,
                         label=m.note or m.source or "", who="", reference=m.reference,
                         amount_in=m.amount if given_back else 0,
                         amount_out=0 if given_back else m.amount))
    for p in q(SupplierPayment, SupplierPayment.expense_id.is_(None)):
        inv = p.invoice
        rows.append(dict(kind="supplier_payment", row=p, date=p.date,
                         label=(inv.number or "") if inv else "",
                         who=inv.supplier.name if inv and inv.supplier else "",
                         reference=p.reference, amount_in=0, amount_out=p.amount))
    for c in q(BankCharge):
        rows.append(dict(kind="bank_charge", row=c, date=c.date, label=c.description or "",
                         who=c.payee, reference=c.reference, amount_in=0, amount_out=c.amount))

    rows.sort(key=lambda r: (r["date"], r["row"].created_at))
    running = acc.opening_balance or 0
    for r in rows:
        running += r["amount_in"] - r["amount_out"]
        r["balance"] = running
    return rows


def balance(acc):
    """What the account holds now: its starting balance, plus what came in,
    less what went out, since that day."""
    rows = account_lines(acc)
    return rows[-1]["balance"] if rows else (acc.opening_balance or 0)


def _company_account_or_404(aid):
    acc = db.session.get(CashAccount, aid)
    if not acc or acc.is_repayable:
        abort(404)
    return acc


@accounts_bp.route("/comptes/<int:aid>")
@login_required
@require_any_perm(*MANAGE)
def detail(aid):
    acc = _company_account_or_404(aid)
    rows = account_lines(acc)
    codes = [acc.ledger_code] + [r["row"].ledger_code for r in rows if r["kind"] == "deposit"]
    return render_template(
        "account_detail.html", acc=acc, rows=list(reversed(rows)),
        balance=rows[-1]["balance"] if rows else (acc.opening_balance or 0),
        total_in=sum(r["amount_in"] for r in rows),
        total_out=sum(r["amount_out"] for r in rows),
        code_labels=labels_for(codes))


@accounts_bp.route("/comptes/<int:aid>/solde-initial", methods=["GET", "POST"])
@login_required
@require_perm(FUND)
def opening(aid):
    acc = _company_account_or_404(aid)
    t = get_t()
    error = None
    if request.method == "POST":
        raw = (request.form.get("opening_balance") or "").strip()
        day = (request.form.get("opening_date") or "").strip()
        amount, error = _amount(raw, t) if raw else (0, None)
        if not error and not _valid_date(day):
            error = t["fund.err.opening_date"]
        if not error:
            acc.opening_balance, acc.opening_date = amount, day
            log_action("UPDATE", "cash_account", resource_id=acc.id,
                       detail="Starting balance of '%s' set to %s GNF on %s"
                              % (acc.name, amount, day))
            db.session.commit()
            flash("success|" + t["fund.opening_saved"])
            return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=acc.id))
    tpl = "_account_opening_form.html" if is_modal_request() else "account_opening_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, acc=acc, error=error, today=date.today().isoformat()), status


def _read_deposit_form():
    """Money put on a company account: which, when, how much, how it came,
    from whom, and what it is on the plan. Returns (data, None) or (None, error)."""
    t = get_t()
    account_id = request.form.get("account_id", type=int) or None
    if not account_id or not CashAccount.query.filter_by(id=account_id, is_active=True,
                                                         is_repayable=False).first():
        return None, t["fund.err.account"]
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["fund.err.date"]
    amount, error = _amount(request.form.get("amount"), t)
    if error:
        return None, error
    if amount <= 0:
        return None, t["invoice.err.amount"]
    method = (request.form.get("method") or "").strip()
    if method not in DEPOSIT_METHODS:
        return None, t["invoice.err.method"]
    source = (request.form.get("source") or "").strip()[:120]
    if not source:
        return None, t["fund.err.source"]
    ledger_code, ok = read_code(request.form, allowed=deposit_accounts())
    if not ok:
        return None, t["ledger.err.unknown"]
    return dict(account_id=account_id, date=date_str, amount=amount, currency="GNF",
                method=method, reference=(request.form.get("reference") or "").strip()[:60] or None,
                source=source, description=(request.form.get("description") or "").strip()[:255] or None,
                ledger_code=ledger_code), None


def _render_deposit_form(row, acc, error=None):
    tpl = "_account_deposit_form.html" if is_modal_request() else "account_deposit_form.html"
    status = 422 if (error and is_modal_request()) else 200
    accounts = (CashAccount.query.filter_by(is_active=True, is_repayable=False)
                .order_by(CashAccount.sort_order, CashAccount.name).all())
    return render_template(tpl, deposit=row, invoice=row, acc=acc, error=error,
                           accounts=accounts, methods=DEPOSIT_METHODS,
                           ledger_accounts=deposit_accounts(),
                           today=date.today().isoformat()), status


def _get_deposit_or_404(did):
    row = db.session.get(AccountDeposit, did)
    if not row:
        abort(404)
    return row


@accounts_bp.route("/comptes/<int:aid>/approvisionner", methods=["GET", "POST"])
@login_required
@require_perm(FUND)
def deposit_new(aid):
    acc = _company_account_or_404(aid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_deposit_form()
        if error:
            return _render_deposit_form(None, acc, error)
        row = AccountDeposit(created_by=current_user.id, **data)
        db.session.add(row)
        db.session.flush()
        perr = _apply_photo_change(row, code="appro-%s" % row.id)
        if perr:
            db.session.rollback()
            return _render_deposit_form(None, acc, perr)
        log_action("CREATE", "account_deposit", resource_id=row.id,
                   detail="%s GNF put on account #%s from %s" % (row.amount, row.account_id, row.source))
        db.session.commit()
        flash("success|" + t["fund.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=row.account_id))
    return _render_deposit_form(None, acc)


@accounts_bp.route("/comptes/approvisionnements/<int:did>/modifier", methods=["GET", "POST"])
@login_required
@require_perm(FUND)
def deposit_edit(did):
    row = _get_deposit_or_404(did)
    t = get_t()
    if request.method == "POST":
        data, error = _read_deposit_form()
        if error:
            return _render_deposit_form(row, row.account, error)
        for k, val in data.items():
            setattr(row, k, val)
        perr = _apply_photo_change(row, code="appro-%s" % row.id)
        if perr:
            db.session.rollback()
            return _render_deposit_form(row, row.account, perr)
        log_action("UPDATE", "account_deposit", resource_id=row.id,
                   detail="Edited money put on account #%s" % row.id)
        db.session.commit()
        flash("success|" + t["fund.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=row.account_id))
    return _render_deposit_form(row, row.account)


@accounts_bp.route("/comptes/approvisionnements/<int:did>/supprimer", methods=["POST"])
@login_required
@require_perm(FUND)
def deposit_delete(did):
    row = _get_deposit_or_404(did)
    aid, photo_key = row.account_id, row.photo_key
    db.session.delete(row)
    log_action("DELETE", "account_deposit", resource_id=did,
               detail="Deleted money put on account #%s" % did)
    db.session.commit()
    # Only once the row is gone for good, as for a bill's photo.
    if photo_key:
        s3_storage.delete_photo(photo_key)
    flash("success|" + get_t()["fund.deleted"])
    return redirect(url_for("accounts.detail", aid=aid))


# ── Sending money to the cash box ────────────────────────────────────────────

TRANSFER_METHODS = ("cash", "mobile_money", "transfer", "cheque")


def _read_transfer_form():
    """Money sent from a company account to the cash box: from which, when,
    how much, how. Returns (data, None) or (None, error)."""
    t = get_t()
    account_id = request.form.get("account_id", type=int) or None
    if not account_id or not CashAccount.query.filter_by(id=account_id, is_active=True,
                                                         is_repayable=False).first():
        return None, t["fund.err.account"]
    date_str = (request.form.get("date") or "").strip()
    if not _valid_date(date_str):
        return None, t["fund.err.date"]
    amount, error = _amount(request.form.get("amount"), t)
    if error:
        return None, error
    if amount <= 0:
        return None, t["invoice.err.amount"]
    method = (request.form.get("method") or "").strip()
    if method not in TRANSFER_METHODS:
        return None, t["invoice.err.method"]
    return dict(account_id=account_id, date=date_str, amount=amount, method=method,
                reference=(request.form.get("reference") or "").strip()[:60] or None,
                note=(request.form.get("note") or "").strip()[:255] or None), None


def _render_transfer_form(row, acc, error=None):
    tpl = "_account_transfer_form.html" if is_modal_request() else "account_transfer_form.html"
    status = 422 if (error and is_modal_request()) else 200
    accounts = (CashAccount.query.filter_by(is_active=True, is_repayable=False)
                .order_by(CashAccount.sort_order, CashAccount.name).all())
    return render_template(tpl, transfer=row, invoice=row, acc=acc, error=error,
                           accounts=accounts, methods=TRANSFER_METHODS,
                           today=date.today().isoformat()), status


def _open_transfer_or_404(tid):
    """A sending Facturation may still change: one the box has not answered."""
    row = db.session.get(CashTransfer, tid)
    if not row:
        abort(404)
    if row.status != "sent":
        flash("error|" + get_t()["transfer.err.answered"])
        return None
    return row


@accounts_bp.route("/comptes/<int:aid>/envoyer-caisse", methods=["GET", "POST"])
@login_required
@require_perm(FUND)
def transfer_new(aid):
    acc = _company_account_or_404(aid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_transfer_form()
        if error:
            return _render_transfer_form(None, acc, error)
        row = CashTransfer(created_by=current_user.id, status="sent", **data)
        db.session.add(row)
        db.session.flush()
        perr = _apply_photo_change(row, code="envoi-caisse-%s" % row.id)
        if perr:
            db.session.rollback()
            return _render_transfer_form(None, acc, perr)
        log_action("CREATE", "cash_transfer", resource_id=row.id,
                   detail="%s GNF sent to the cash box from account #%s" % (row.amount, row.account_id))
        db.session.commit()
        flash("success|" + t["transfer.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=row.account_id))
    return _render_transfer_form(None, acc)


@accounts_bp.route("/comptes/envois/<int:tid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm(FUND)
def transfer_edit(tid):
    row = _open_transfer_or_404(tid)
    if row is None:
        return redirect(request.referrer or url_for("accounts.index"))
    t = get_t()
    if request.method == "POST":
        data, error = _read_transfer_form()
        if error:
            return _render_transfer_form(row, row.account, error)
        for k, val in data.items():
            setattr(row, k, val)
        perr = _apply_photo_change(row, code="envoi-caisse-%s" % row.id)
        if perr:
            db.session.rollback()
            return _render_transfer_form(row, row.account, perr)
        log_action("UPDATE", "cash_transfer", resource_id=row.id,
                   detail="Edited sending to the cash box #%s" % row.id)
        db.session.commit()
        flash("success|" + t["transfer.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("accounts.detail", aid=row.account_id))
    return _render_transfer_form(row, row.account)


@accounts_bp.route("/comptes/envois/<int:tid>/supprimer", methods=["POST"])
@login_required
@require_perm(FUND)
def transfer_delete(tid):
    row = _open_transfer_or_404(tid)
    if row is None:
        return redirect(request.referrer or url_for("accounts.index"))
    aid, photo_key = row.account_id, row.photo_key
    db.session.delete(row)
    log_action("DELETE", "cash_transfer", resource_id=tid,
               detail="Deleted sending to the cash box #%s" % tid)
    db.session.commit()
    if photo_key:
        s3_storage.delete_photo(photo_key)
    flash("success|" + get_t()["transfer.deleted"])
    return redirect(url_for("accounts.detail", aid=aid))
