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
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import get_t, is_modal_request, log_action, modal_ok, require_any_perm
from blueprints.expenses import _save_list_row
from ledger import labels_for, read_code, set_till_code, till_code, used_accounts_in
from models import BankCharge, CashAccount, CashMovement, Expense, SupplierPayment, db

accounts_bp = Blueprint("accounts", __name__)

# Who may keep the list: the cashier, whoever records supplier bills, and
# whoever records what clients pay.
MANAGE = ("expense.create", "supplier_invoice.create", "invoicing.manage")


def _used_ids():
    """Accounts something points at, which may be archived but never deleted:
    a money-in or money-out of the box, a cost an account paid directly, or an
    instalment on a supplier's bill."""
    used = set()
    for model, col in ((CashMovement, CashMovement.account_id),
                       (Expense, Expense.account_id),
                       (SupplierPayment, SupplierPayment.account_id),
                       (BankCharge, BankCharge.account_id)):
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
    return render_template(
        "accounts.html",
        accounts=CashAccount.query.order_by(CashAccount.sort_order,
                                            CashAccount.name).all(),
        used=_used_ids(), paid=_paid_to_suppliers(),
        till=till_code(), till_accounts=used_accounts_in((5,)),
        code_labels=labels_for([a.ledger_code for a in CashAccount.query.all()] + [till_code()]))


@accounts_bp.route("/comptes/caisse", methods=["POST"])
@login_required
@require_any_perm(*MANAGE)
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
