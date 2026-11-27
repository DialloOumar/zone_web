"""Importing an old supplier ledger from Excel: upload, review, import.

The file is read once and kept with the answers given on it. The review goes
sheet by sheet: which supplier of the app it is, which of its lines count in
the solde à nouveau, and which bills or orders of the app are lines of the
sheet too. Each sheet is validated by hand, or left out, and the import
button stays locked until all of them are. Nothing reaches the suppliers,
their bills or their balances before that click. Given by its own
permission (supplier_import.manage), to the comptable typically.
"""
import json
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import ledger_import
from app import get_t, has_perm, log_action
from blueprints.supplier_invoices import orders_to_bill
from models import Supplier, SupplierImport, SupplierInvoice, db

supplier_import_bp = Blueprint("supplier_import", __name__)


def _guard():
    if not has_perm("supplier_import.manage"):
        abort(403)


def _get_or_404(iid):
    imp = db.session.get(SupplierImport, iid)
    if not imp:
        abort(404)
    return imp


def _draft_or_409(iid):
    imp = _get_or_404(iid)
    if imp.status != "draft":
        abort(409)
    return imp


def _review(imp):
    """The sheets read against what the app holds today."""
    suppliers = Supplier.query.order_by(Supplier.name).all()
    open_bills, opening_rows = {}, {}
    for b in SupplierInvoice.query.order_by(SupplierInvoice.date, SupplierInvoice.id):
        if b.is_opening:
            opening_rows[b.supplier_id] = b
        elif b.remaining > 0:
            open_bills.setdefault(b.supplier_id, []).append(b)
    orders = {}
    for po in orders_to_bill():
        orders.setdefault(po.supplier_id, []).append(po)
    rows, groups = ledger_import.review(imp, suppliers, open_bills, opening_rows, orders)
    return suppliers, rows, groups


def _counts(rows, groups):
    def n(*states):
        return sum(1 for r in rows if r.get("state") in states)
    return dict(
        sheets=sum(1 for r in rows if r["readable"]),
        ok=n("ok"), skip=n("skip"), todo=n("todo", "check"),
        simple=sum(1 for r in rows if r.get("state") == "check" and r.get("simple")),
        new=sum(1 for g in groups.values() if g["target"] == "new"),
        linked=sum(1 for g in groups.values() if g["target"] != "new"),
        total=sum(g["opening"] for g in groups.values() if not g["keep_old"]),
    )


def _save(imp, ans):
    imp.choices = json.dumps(ans)
    db.session.commit()


@supplier_import_bp.route("/fournisseurs/import", methods=["GET", "POST"])
@login_required
def index():
    _guard()
    t = get_t()
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("error|" + t["imp.err.file"])
            return redirect(url_for("supplier_import.index"))
        try:
            sheets, as_of = ledger_import.read_workbook(f.read())
        except Exception:
            flash("error|" + t["imp.err.unreadable"])
            return redirect(url_for("supplier_import.index"))
        if not any(s["readable"] for s in sheets):
            flash("error|" + t["imp.err.no_sheet"])
            return redirect(url_for("supplier_import.index"))
        imp = SupplierImport(filename=f.filename[:200], as_of=as_of, data=json.dumps(sheets),
                             choices="{}", created_by=current_user.id)
        db.session.add(imp)
        db.session.flush()
        log_action("CREATE", "supplier_import", resource_id=imp.id,
                   detail="Read supplier ledger '%s': %d sheets" % (imp.filename, len(sheets)))
        db.session.commit()
        return redirect(url_for("supplier_import.review", iid=imp.id))
    imports = SupplierImport.query.order_by(SupplierImport.created_at.desc()).all()
    return render_template("supplier_imports.html", imports=imports)


@supplier_import_bp.route("/fournisseurs/import/<int:iid>")
@login_required
def review(iid):
    """Every sheet of the file, where it stands, and the import button."""
    _guard()
    imp = _get_or_404(iid)
    if imp.status == "done":
        return render_template("supplier_import_done.html", imp=imp,
                               result=json.loads(imp.choices).get("_result") or {})
    suppliers, rows, groups = _review(imp)
    state = request.args.get("etat") or ""
    if state not in ("", "todo", "ok", "skip"):
        state = ""
    shown = [r for r in rows if r["readable"] and (not state or r["state"] == state
                                                    or (state == "todo" and r["state"] == "check"))]
    return render_template("supplier_import.html", imp=imp, rows=shown, groups=groups,
                           counts=_counts(rows, groups), state=state)


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/feuille/<int:i>", methods=["GET", "POST"])
@login_required
def sheet(iid, i):
    """One sheet: who it is in the app, its lines to keep or not, what the app
    already holds for that supplier. Saved as a whole; "Valider" also marks
    it done and moves on to the next sheet still to see."""
    _guard()
    t = get_t()
    imp = _get_or_404(iid)
    if imp.status == "done":
        return redirect(url_for("supplier_import.review", iid=iid))
    sheets = json.loads(imp.data)
    if not 0 <= i < len(sheets) or not sheets[i]["readable"]:
        abort(404)
    if request.method == "POST":
        ans = json.loads(imp.choices or "{}")
        f = request.form
        action = f.get("action") or "save"
        p = "%d:" % i
        if action == "skip":
            ans[p + "skip"] = "1"
            ans.pop(p + "ok", None)
        elif action == "unskip":
            ans.pop(p + "skip", None)
        else:
            match = (f.get("match") or "").strip()
            if match:
                ans[p + "match"] = match[:20]
            if "name" in f:
                name = (f.get("name") or "").strip()
                if name and name != sheets[i]["name"]:
                    ans[p + "name"] = name[:120]
                else:
                    ans.pop(p + "name", None)
            if f.get("type") in ("parts", "machines", "both"):
                ans[p + "type"] = f.get("type")
            if "lines" in f:
                kept = {int(x) for x in f.getlist("keep") if x.isdigit()}
                ans[p + "off"] = [n for n in range(len(sheets[i]["lines"])) if n not in kept]
            for k, v in f.items():
                if k.startswith(("inv:", "po:", "open:")) and len(k) <= 30 and v:
                    if v == "other":
                        v = f.get("other:" + k) or ""
                    if v:
                        ans[k] = v[:20]
            ans.pop(p + "ok", None)
        _save(imp, ans)
        if action == "ok":
            suppliers, rows, groups = _review(imp)
            r = next(x for x in rows if x["i"] == i)
            if r["blocks"]:
                flash("error|" + t["imp.err.blocked"])
                return redirect(url_for("supplier_import.sheet", iid=iid, i=i))
            ans[p + "ok"] = "1"
            _save(imp, ans)
        if action in ("ok", "skip"):
            return redirect(_next_url(imp, i))
        return redirect(url_for("supplier_import.sheet", iid=iid, i=i))
    suppliers, rows, groups = _review(imp)
    r = next(x for x in rows if x["i"] == i)
    readable = [x for x in rows if x["readable"]]
    at = readable.index(r)
    return render_template("supplier_import_sheet.html", imp=imp, r=r, rows=rows, suppliers=suppliers,
                           group=groups.get(r.get("group")), pos=at + 1, n=len(readable),
                           prev=readable[at - 1] if at > 0 else None,
                           nxt=readable[at + 1] if at + 1 < len(readable) else None)


def _next_url(imp, i):
    """The next sheet still to see after sheet i, or the list when none is."""
    suppliers, rows, groups = _review(imp)
    readable = [r for r in rows if r["readable"]]
    at = next(k for k, r in enumerate(readable) if r["i"] == i)
    for r in readable[at + 1:] + readable[:at]:
        if r["state"] in ("check", "todo"):
            return url_for("supplier_import.sheet", iid=imp.id, i=r["i"])
    return url_for("supplier_import.review", iid=imp.id)


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/simples", methods=["POST"])
@login_required
def validate_simple(iid):
    """Every sheet with nothing to decide validated at once."""
    _guard()
    imp = _draft_or_409(iid)
    suppliers, rows, groups = _review(imp)
    ans = json.loads(imp.choices or "{}")
    n = 0
    for r in rows:
        if r.get("state") == "check" and r.get("simple"):
            ans["%d:ok" % r["i"]] = "1"
            n += 1
    _save(imp, ans)
    flash("success|" + get_t()["imp.simple_done"] % n)
    return redirect(url_for("supplier_import.review", iid=iid))


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/date", methods=["POST"])
@login_required
def as_of(iid):
    """The day the balances stood on: the date of every solde antérieur, and
    the last day of the bills of the app put against the file."""
    _guard()
    imp = _draft_or_409(iid)
    value = (request.form.get("as_of") or "").strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        flash("error|" + get_t()["opening.err.date"])
        return redirect(url_for("supplier_import.review", iid=iid))
    imp.as_of = value
    db.session.commit()
    return redirect(url_for("supplier_import.review", iid=iid))


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/importer", methods=["POST"])
@login_required
def run(iid):
    """Everything at once, or nothing: new suppliers, what they provide, the
    bills of the orders billed in the file, and each solde antérieur, dated
    on the day the balances stood on."""
    _guard()
    t = get_t()
    imp = _draft_or_409(iid)
    suppliers, rows, groups = _review(imp)
    left = sum(1 for r in rows if r["readable"] and r["state"] not in ("ok", "skip"))
    if left:
        flash("error|" + t["imp.err.still_open"] % left)
        return redirect(url_for("supplier_import.review", iid=iid))
    made = dict(new=0, linked=0, openings=0, bills=0)
    result = []
    for g in groups.values():
        h, kind = g["head"], g["kind"]
        parts, machines = kind in ("parts", "both"), kind in ("machines", "both")
        if g["target"] == "new":
            sup = Supplier(name=h["name"].strip(), kind="permanent",
                           provides_parts=parts, provides_machines=machines)
            db.session.add(sup)
            db.session.flush()
            made["new"] += 1
        else:
            sup = g["target"]
            sup.provides_parts = sup.provides_parts or parts
            sup.provides_machines = sup.provides_machines or machines
            made["linked"] += 1
        for po, line in g["bills"]:
            day = line["day"]
            iso = "%s-%s-%s" % (day[6:10], day[3:5], day[0:2]) if len(day) == 10 and day[2] == "/" else imp.as_of
            db.session.add(SupplierInvoice(
                supplier_id=sup.id, purchase_order_id=po.id, number=(line["ref"] or None) and line["ref"][:60],
                date=iso, amount=po.total, currency="GNF",
                description=(line["text"] or "")[:255] or None, created_by=current_user.id))
            made["bills"] += 1
        result.append(dict(sheets=[r["sheet"] for r in g["rows"]], sid=sup.id, name=sup.name, code=sup.code,
                           new=g["target"] == "new", kind=kind, bills=len(g["bills"]),
                           opening=g["opening"], kept=g["keep_old"]))
        if g["keep_old"]:
            continue
        amount, old = g["opening"], g["existing"]
        if amount > 0:
            if old is None:
                old = SupplierInvoice(supplier_id=sup.id, is_opening=True, currency="GNF",
                                      created_by=current_user.id)
                db.session.add(old)
            old.date, old.amount, old.description = imp.as_of, amount, t["opening.subject"]
            made["openings"] += 1
        elif old is not None and not old.payments:
            db.session.delete(old)
    # What was done, kept with the answers: once imported, the file read
    # against the app as it now is would no longer say what happened.
    ans = json.loads(imp.choices or "{}")
    ans["_result"] = dict(groups=result, skipped=[r["sheet"] for r in rows if r.get("state") == "skip"])
    imp.choices = json.dumps(ans)
    imp.status, imp.done_by, imp.done_at = "done", current_user.id, datetime.utcnow()
    log_action("IMPORT", "supplier_import", resource_id=imp.id,
               detail="Imported '%s' as of %s: %d new suppliers, %d existing, %d opening balances, %d bills"
                      % (imp.filename, imp.as_of, made["new"], made["linked"], made["openings"], made["bills"]))
    db.session.commit()
    flash("success|" + t["imp.done"] % made)
    return redirect(url_for("supplier_import.review", iid=iid))


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/supprimer", methods=["POST"])
@login_required
def delete(iid):
    """A review dropped. An import already done stays, as its record."""
    _guard()
    imp = _draft_or_409(iid)
    log_action("DELETE", "supplier_import", resource_id=imp.id, detail="Dropped review of '%s'" % imp.filename)
    db.session.delete(imp)
    db.session.commit()
    flash("success|" + get_t()["imp.deleted"])
    return redirect(url_for("supplier_import.index"))
