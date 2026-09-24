"""Finance — the accounting pages of the Finance section of the drawer.

The section holds the money pages everyone uses (Caisse, Factures
fournisseurs, Facturation, Comptes) and, around them, the accounting ones
kept here: the plan, and the journal to come. These are
reached by the super admins and nobody else while they are being built;
every address under /finance answers "page not found" to anyone else, so an
unfinished screen is never seen by a user.

One guard at the door, below, covers every page added here later. When the
pages are ready for a comptable, that guard is where a permission replaces
the super-admin rule — the pages themselves will not change.

Plan comptable
--------------
The SYSCOHADA révisé chart, seeded on boot (see _seed_plan_comptable_data in
app.py), shown one class at a time as a tree, searchable on code or label.
The company adds its own accounts under any standard one — a sub-account per
supplier under 4011, per bank under 521. A standard account is never edited
or deleted, only hidden from the pickers; a company account can be renamed,
and removed while nothing points at it. Nothing points at any of them yet:
attaching accounts to costs is the next step.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from app import can_enter_finance, get_t, is_modal_request, log_action, login_manager, modal_ok
from models import LedgerAccount, db

finance_bp = Blueprint("finance", __name__, url_prefix="/finance")

# The nine classes of the plan, in the words the plan uses for them.
CLASSES = [
    (1, "Comptes de ressources durables"),
    (2, "Comptes d'actif immobilisé"),
    (3, "Comptes de stocks"),
    (4, "Comptes de tiers"),
    (5, "Comptes de trésorerie"),
    (6, "Comptes de charges des activités ordinaires"),
    (7, "Comptes de produits des activités ordinaires"),
    (8, "Comptes des autres charges et des autres produits"),
    (9, "Comptes des engagements hors bilan et de la comptabilité analytique"),
]


@finance_bp.before_request
def _guard():
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not can_enter_finance():
        abort(404)


@finance_bp.route("/")
def index():
    """The old workspace's front door: the plan is the section's first page."""
    return redirect(url_for("finance.plan"))


# ── Plan comptable ───────────────────────────────────────────────────────────

def _tree(accounts):
    """Accounts in chart order: by code, which for a fixed-width numbering is
    also parent-before-children. Each row knows whether it has children so
    the page can draw it as a heading."""
    ordered = sorted(accounts, key=lambda a: a.code)
    parents = {a.parent_code for a in ordered if a.parent_code}
    return [(a, a.code in parents) for a in ordered]


@finance_bp.route("/plan-comptable")
def plan():
    q = (request.args.get("q") or "").strip()
    klass = request.args.get("classe", type=int)
    show_hidden = request.args.get("masques") == "1"

    base = LedgerAccount.query
    if not show_hidden:
        base = base.filter(LedgerAccount.is_active.is_(True))

    counts = dict(db.session.query(LedgerAccount.klass, db.func.count(LedgerAccount.id))
                  .filter(LedgerAccount.is_active.is_(True))
                  .group_by(LedgerAccount.klass).all())
    own = dict(db.session.query(LedgerAccount.klass, db.func.count(LedgerAccount.id))
               .filter(LedgerAccount.is_standard.is_(False))
               .group_by(LedgerAccount.klass).all())
    hidden_count = LedgerAccount.query.filter(LedgerAccount.is_active.is_(False)).count()

    rows = None
    if q:
        like = f"%{q}%"
        if q.isdigit():
            rows = base.filter(LedgerAccount.code.like(q + "%")).all()
        else:
            rows = base.filter(LedgerAccount.label.ilike(like)).all()
        rows = _tree(rows)
    elif klass:
        rows = _tree(base.filter(LedgerAccount.klass == klass).all())

    return render_template("finance/plan_comptable.html",
                           classes=CLASSES, counts=counts, own=own,
                           hidden_count=hidden_count, klass=klass, q=q,
                           show_hidden=show_hidden, rows=rows)


def _render_form(row, parent, error=None):
    tpl = ("finance/_ledger_account_form.html" if is_modal_request()
           else "finance/ledger_account_form.html")
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, parent=parent, error=error), status


def _back(parent_code=None, code=None):
    """Where to land after a save: the class the account lives in."""
    ref = code or parent_code or ""
    return redirect(url_for("finance.plan", classe=int(ref[0]) if ref else None))


def _save(row, parent):
    """Read the form into `row` (a new one when None). Returns an error
    message, or None once the row is in the session."""
    t = get_t()
    code = (request.form.get("code") or "").strip()
    label = (request.form.get("label") or "").strip()
    if not label:
        return t.get("plan.err.label", "Le libellé est obligatoire.")
    if row is None:
        if not parent:
            return t.get("plan.err.parent", "Choisissez le compte parent.")
        if not code.isdigit() or len(code) > 12:
            return t.get("plan.err.code", "Le numéro est composé de chiffres, 12 au plus.")
        if not code.startswith(parent.code) or code == parent.code:
            return t.get("plan.err.code_parent",
                         "Le numéro doit commencer par celui du compte parent.")
        if LedgerAccount.query.filter_by(code=code).first():
            return t.get("plan.err.code_taken", "Ce numéro existe déjà.")
        row = LedgerAccount(code=code, klass=int(code[0]), parent_code=parent.code,
                            is_standard=False, created_by=current_user.id)
        db.session.add(row)
        log_action("CREATE", "ledger_account", detail=f"Added account {code} '{label}'")
    else:
        log_action("UPDATE", "ledger_account", resource_id=row.id,
                   detail=f"Renamed account {row.code} '{row.label}' -> '{label}'")
    row.label = label
    return None


@finance_bp.route("/plan-comptable/nouveau", methods=["GET", "POST"])
def account_new():
    parent_code = (request.values.get("parent") or "").strip()
    parent = LedgerAccount.query.filter_by(code=parent_code).first() if parent_code else None
    if request.method == "POST":
        error = _save(None, parent)
        if error:
            return _render_form(None, parent, error)
        db.session.commit()
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else _back(parent_code)
    return _render_form(None, parent)


@finance_bp.route("/plan-comptable/<int:aid>/modifier", methods=["GET", "POST"])
def account_edit(aid):
    row = db.session.get(LedgerAccount, aid)
    if not row or row.is_standard:
        abort(404)
    parent = LedgerAccount.query.filter_by(code=row.parent_code).first()
    if request.method == "POST":
        error = _save(row, parent)
        if error:
            return _render_form(row, parent, error)
        db.session.commit()
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else _back(code=row.code)
    return _render_form(row, parent)


@finance_bp.route("/plan-comptable/<int:aid>/<any(hide,show,delete):what>", methods=["POST"])
def account_action(aid, what):
    """Hide takes an account out of the pickers, standard or not; delete is
    for a company account only, and nothing points at any account yet."""
    row = db.session.get(LedgerAccount, aid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if row.is_standard:
            abort(404)
        if LedgerAccount.query.filter_by(parent_code=row.code).first():
            flash("error|" + t.get("plan.err.has_children",
                                   "Impossible de supprimer : ce compte a des sous-comptes."))
            return _back(code=row.code)
        log_action("DELETE", "ledger_account", resource_id=aid,
                   detail=f"Deleted account {row.code} '{row.label}'")
        db.session.delete(row)
        msg = "list.deleted"
    else:
        row.is_active = what == "show"
        log_action(what.upper(), "ledger_account", resource_id=aid,
                   detail=f"{what.title()} account {row.code}")
        msg = "plan.hidden" if what == "hide" else "plan.shown"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return _back(code=row.code)
