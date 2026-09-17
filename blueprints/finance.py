"""Finance workspace — the accounting side of the app, built little by little.

Like batmex's BATMEX SA or HR: its own sidebar behind a switcher at the top of
the drawer, reached only by whoever the switcher is shown to. Today that is
the super admins and nobody else; every address under /finance answers "page
not found" to anyone else, so an unfinished screen is never seen by a user.

One guard at the door, below, covers every page added here later. When the
workspace is ready for a comptable, that guard is where a permission replaces
the super-admin rule — the pages themselves will not change.

Nothing is stubbed: the sidebar lists what works and nothing else.

Plan comptable
--------------
The SYSCOHADA révisé chart, seeded on boot (see _seed_plan_comptable_data in
app.py), shown one class at a time as a tree, searchable on code or label.
The company adds its own accounts under any standard one — a sub-account per
supplier under 4011, per bank under 521. A standard account is never edited
or deleted, only hidden from the pickers; a company account can be renamed,
and removed while nothing points at it. Nothing points at any of them yet:
attaching accounts to costs is the next step.

Paramétrage comptable
---------------------
Where each kind of money line lands by default: what a repair is debited to,
what the cash box or a bank account is credited from, under which account the
suppliers and the staff sit. Declared below in MAPPING_GROUPS with a proposed
code each, stored in finance_settings once someone saves the page. An empty
mapping means "decided by hand, line by line" -- the honest answer for a cost
the app knows nothing about, like a free-text terrain expense. The proposed
codes are a starting point for the comptable, not a ruling.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from app import can_enter_finance, get_t, is_modal_request, log_action, login_manager, modal_ok
from models import CashAccount, FinanceSetting, LedgerAccount, db

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
    return render_template("finance/home.html")


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


# ── Paramétrage comptable ────────────────────────────────────────────────────

# (key, proposed code), grouped, with the class the picker offers. The label
# and the note for each key live in languages.py under "param.key.<key>".
MAPPING_GROUPS = [
    ("costs", 6, [
        ("cost.entretien", "6242"),   # maintenance fiches
        ("cost.pieces",    "6041"),   # parts bought for the store
        ("cost.fuel",      "6042"),   # fuel bought (old rows, and fuel bills)
        ("cost.location",  "6223"),   # a lessor's bill for its machines
        ("cost.lavage",    "6248"),
        ("cost.accident",  "6242"),
        ("cost.terrain",   None),     # free-text field cost: coded by hand
        ("cost.societe",   None),     # free-text office cost: coded by hand
    ]),
    ("purses", 5, [
        ("purse.caisse",              "571"),
        ("purse.method.mobile_money", "552"),
        ("purse.method.transfer",     "521"),
        ("purse.method.cheque",       "521"),
        # one row per CashAccount is added at render time: purse.account.<id>
    ]),
    ("tiers", 4, [
        ("tiers.supplier", "4011"),   # parent of the per-supplier sub-accounts
        ("tiers.staff",    "421"),    # advances and what is owed to staff
        ("tiers.client",   "4111"),   # the client, when billing moves here
    ]),
    ("revenue", 7, [
        ("revenue.client", "706"),    # what the client is billed for
    ]),
]

TVA_DEFAULTS = {"tva.assujettie": "0", "tva.rate": "18"}


def _proposed():
    out = {}
    for _, _, keys in MAPPING_GROUPS:
        out.update(dict(keys))
    return out


def finance_setting(key, default=None):
    """The saved value for a key, else what the page proposes, else `default`.
    This is what the coding of costs (next step) asks."""
    row = db.session.get(FinanceSetting, key)
    if row is not None:
        return row.value
    return _proposed().get(key, TVA_DEFAULTS.get(key, default))


def _mapping_rows():
    """Every mapping row the page shows, group by group, with the value in
    force and whether it was saved or is only proposed."""
    saved = {r.key: r for r in FinanceSetting.query.all()}
    groups = []
    for gname, klass, keys in MAPPING_GROUPS:
        keys = list(keys)
        if gname == "purses":
            for acc in (CashAccount.query.filter_by(is_active=True)
                        .order_by(CashAccount.sort_order, CashAccount.name)):
                keys.append((f"purse.account.{acc.id}", None))
        rows = []
        for key, default in keys:
            row = saved.get(key)
            value = row.value if row is not None else default
            rows.append({"key": key, "value": value or "", "saved": row is not None})
        groups.append((gname, klass, rows))
    return groups


@finance_bp.route("/parametrage", methods=["GET", "POST"])
def settings():
    t = get_t()
    groups = _mapping_rows()
    accounts = LedgerAccount.query.filter_by(is_active=True).order_by(LedgerAccount.code).all()
    by_code = {a.code: a for a in accounts}
    by_class = {}
    for a in accounts:
        by_class.setdefault(a.klass, []).append(a)
    purses = {f"purse.account.{a.id}": a.name
              for a in CashAccount.query.filter_by(is_active=True).all()}
    tva = {k: finance_setting(k) for k in TVA_DEFAULTS}

    errors = {}
    if request.method == "POST":
        form = {}
        for _, _, rows in groups:
            for r in rows:
                code = (request.form.get(r["key"]) or "").strip()
                if code and code not in by_code:
                    errors[r["key"]] = t.get("param.err.unknown", "Ce numéro n'est pas dans le plan.")
                form[r["key"]] = code
        assujettie = "1" if request.form.get("tva.assujettie") else "0"
        rate = (request.form.get("tva.rate") or "").strip()
        if not rate.isdigit() or not 0 <= int(rate) <= 100:
            errors["tva.rate"] = t.get("param.err.rate", "Taux entre 0 et 100.")
        if errors:
            for _, _, rows in groups:
                for r in rows:
                    r["value"] = form.get(r["key"], r["value"])
            tva = {"tva.assujettie": assujettie, "tva.rate": rate}
        else:
            changed = 0
            for key, value in list(form.items()) + [("tva.assujettie", assujettie), ("tva.rate", rate)]:
                row = db.session.get(FinanceSetting, key)
                if row is None:
                    db.session.add(FinanceSetting(key=key, value=value or None,
                                                  updated_by=current_user.id))
                    changed += 1
                elif (row.value or "") != (value or ""):
                    row.value = value or None
                    row.updated_by = current_user.id
                    changed += 1
            db.session.commit()
            log_action("UPDATE", "finance_settings",
                       detail=f"Accounting map saved, {changed} keys changed")
            flash("success|" + t.get("param.saved", "Paramétrage enregistré."))
            return redirect(url_for("finance.settings"))

    return render_template("finance/parametrage.html", groups=groups, by_code=by_code,
                           by_class=by_class, purses=purses, tva=tva, errors=errors)
