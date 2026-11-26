"""Importing an old supplier ledger from Excel: upload, review, import.

The file is read once and kept with the answers given on it. The review
page lists every sheet; the points the app cannot decide alone are left
without an answer, and the import button stays locked until none is left.
Nothing reaches the suppliers, their bills or their balances before that
click. Super admin only.
"""
import json
from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import ledger_import
from app import get_t, is_modal_request, log_action
from blueprints.supplier_invoices import orders_to_bill
from models import Supplier, SupplierImport, SupplierInvoice, db

supplier_import_bp = Blueprint("supplier_import", __name__)


def _guard():
    if not current_user.is_super_admin:
        abort(403)


def _get_or_404(iid):
    imp = db.session.get(SupplierImport, iid)
    if not imp:
        abort(404)
    return imp


def _review(imp):
    """The sheets read against what the app holds today."""
    t = get_t()
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
    rows, groups, open_n = ledger_import.review(imp, t, suppliers, open_bills, opening_rows, orders)
    return suppliers, rows, groups, open_n


def _counts(rows, groups):
    return dict(
        sheets=sum(1 for r in rows if r["status"] != "unread"),
        open=sum(r["open"] for r in rows),
        rows_open=sum(1 for r in rows if r["state"] == "open"),
        skip=sum(1 for r in rows if r["state"] == "skip" and r["status"] != "unread"),
        new=sum(1 for g in groups.values() if g["target"] == "new"),
        linked=sum(1 for g in groups.values() if g["target"] not in (None, "new")),
        total=sum(g["opening"] or 0 for g in groups.values() if not g.get("keep_old")),
    )


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
    _guard()
    imp = _get_or_404(iid)
    if imp.status == "done":
        return render_template("supplier_import_done.html", imp=imp,
                               result=json.loads(imp.choices).get("_result") or {})
    suppliers, rows, groups, open_n = _review(imp)
    return render_template("supplier_import.html", imp=imp, rows=rows, groups=groups,
                           suppliers=suppliers, counts=_counts(rows, groups),
                           show=request.args.get("show") or ("open" if open_n else "all"))


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/choix", methods=["POST"])
@login_required
def answer(iid):
    """One answer, saved as soon as it is picked. Answers lean on each other
    (a sheet merged into another, a supplier changed), so the whole table
    comes back."""
    _guard()
    imp = _get_or_404(iid)
    if imp.status != "draft":
        abort(409)
    key = (request.form.get("key") or "").strip()
    value = (request.form.get("value") or "").strip()
    if key == "as_of":
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return jsonify(error=get_t()["opening.err.date"]), 422
        imp.as_of = value
    else:
        ans = json.loads(imp.choices or "{}")
        if not key or len(key) > 40 or len(value) > 120:
            abort(400)
        if value:
            ans[key] = value
        else:
            ans.pop(key, None)
        imp.choices = json.dumps(ans)
    db.session.commit()
    suppliers, rows, groups, open_n = _review(imp)
    counts = _counts(rows, groups)
    return jsonify(
        body=render_template("_supplier_import_rows.html", imp=imp, rows=rows, groups=groups, suppliers=suppliers),
        counters=render_template("_supplier_import_counts.html", imp=imp, counts=counts),
        open=counts["open"])


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/feuille/<int:i>")
@login_required
def sheet(iid, i):
    _guard()
    imp = _get_or_404(iid)
    sheets = json.loads(imp.data)
    if not 0 <= i < len(sheets):
        abort(404)
    tpl = "_supplier_import_sheet.html" if is_modal_request() else "supplier_import_sheet.html"
    return render_template(tpl, imp=imp, s=sheets[i])


@supplier_import_bp.route("/fournisseurs/import/<int:iid>/importer", methods=["POST"])
@login_required
def run(iid):
    """Everything at once, or nothing: new suppliers, what they provide, the
    bills of the orders already billed in the file, and each solde antérieur,
    dated on the day the balances stood on."""
    _guard()
    t = get_t()
    imp = _get_or_404(iid)
    if imp.status != "draft":
        abort(409)
    suppliers, rows, groups, open_n = _review(imp)
    if open_n:
        flash("error|" + t["imp.err.still_open"] % open_n)
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
            db.session.add(SupplierInvoice(
                supplier_id=sup.id, purchase_order_id=po.id, number=(line[3] or None) and line[3][:60],
                date=line[0] or imp.as_of, amount=po.total, currency="GNF",
                description=(line[2] or "")[:255] or None, created_by=current_user.id))
            made["bills"] += 1
        result.append(dict(sheets=[r["sheet"] for r in g["rows"]], sid=sup.id, name=sup.name, code=sup.code,
                           new=g["target"] == "new", kind=kind, bills=len(g["bills"]),
                           opening=g["opening"] or 0, kept=bool(g.get("keep_old"))))
        if g.get("keep_old"):
            continue
        amount, old = g["opening"] or 0, g.get("existing")
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
    ans["_result"] = dict(groups=result, skipped=[r["sheet"] for r in rows if r["status"] == "skip"])
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
    imp = _get_or_404(iid)
    if imp.status != "draft":
        abort(409)
    log_action("DELETE", "supplier_import", resource_id=imp.id, detail="Dropped review of '%s'" % imp.filename)
    db.session.delete(imp)
    db.session.commit()
    flash("success|" + get_t()["imp.deleted"])
    return redirect(url_for("supplier_import.index"))
