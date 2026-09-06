"""Personnel — who works for the company, and what has been spent on them.

The first stone of an HR module. Today it does one job: hold the people, so a
cost on the Dépenses page can say who it went out for — an advance, a mission,
a phone bill. Contracts, leave and pay come later and hang off the same rows.

Not the operators table. An operator is a driver attached to one client's
fleet and that list feeds the roster; the storekeeper and the accountant have
no business in a pointing dropdown. A driver who is also on the payroll
therefore sits in both lists for now.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import get_t, is_modal_request, log_action, modal_ok, require_perm
from models import Expense, Staff, StaffPosition, db

staff_bp = Blueprint("staff", __name__)

PER_PAGE = 50


# ── Helpers ──────────────────────────────────────────────────────────────────


def active_staff():
    """Who a cost can be attributed to. Someone archived keeps the costs
    already recorded against them, they are simply no longer offered."""
    return (Staff.query.filter(Staff.is_active.is_(True))
            .order_by(Staff.name).all())


def active_positions():
    return (StaffPosition.query.filter(StaffPosition.is_active.is_(True))
            .order_by(StaffPosition.sort_order, StaffPosition.name).all())


def _valid_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _ids(name):
    out = []
    for raw in request.args.getlist(name):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            pass
    return out


def _spend_by_staff():
    """What has been spent on each person, in one grouped query rather than one
    per row of the list."""
    rows = (db.session.query(
        Expense.staff_id,
        db.func.count(Expense.id),
        db.func.coalesce(db.func.sum(Expense.amount), 0))
        .filter(Expense.staff_id.isnot(None))
        .group_by(Expense.staff_id).all())
    return {r[0]: {"count": int(r[1]), "total": int(r[2] or 0)} for r in rows}


# ── The person's form ────────────────────────────────────────────────────────


def _read_staff_form(row):
    """Returns (data, None) or (None, error)."""
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return None, t["staff.err.name_required"]

    clash = Staff.query.filter(db.func.lower(Staff.name) == name.lower())
    if row:
        clash = clash.filter(Staff.id != row.id)
    if clash.first():
        return None, t["staff.err.name_taken"]

    position_id = request.form.get("position_id", type=int) or None
    if position_id and not StaffPosition.query.filter_by(
            id=position_id, is_active=True).first():
        return None, t["staff.err.position"]

    hired_on = (request.form.get("hired_on") or "").strip()
    if hired_on and not _valid_date(hired_on):
        return None, t["staff.err.hired_on"]

    return dict(
        name=name,
        position_id=position_id,
        phone=(request.form.get("phone") or "").strip() or None,
        matricule=(request.form.get("matricule") or "").strip() or None,
        hired_on=hired_on or None,
        note=(request.form.get("note") or "").strip() or None,
    ), None


def _render_staff_form(row, error=None):
    tpl = "_staff_form.html" if is_modal_request() else "staff_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error,
                           positions=active_positions(),
                           today=date.today().isoformat()), status


# ── Routes: the list ─────────────────────────────────────────────────────────


@staff_bp.route("/personnel")
@login_required
@require_perm("staff.view")
def index():
    position_ids = _ids("position")
    search = (request.args.get("q") or "").strip()
    # Archived people are out of the way by default, and one tick brings the
    # whole payroll back — including whoever left in March.
    show_archived = request.args.get("archived") == "1"

    q = Staff.query
    if not show_archived:
        q = q.filter(Staff.is_active.is_(True))
    if position_ids:
        q = q.filter(Staff.position_id.in_(position_ids))
    if search:
        like = "%" + search + "%"
        q = q.filter(db.or_(Staff.name.ilike(like), Staff.matricule.ilike(like),
                            Staff.phone.ilike(like)))

    pagination = q.order_by(Staff.name).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE,
        error_out=False)

    return render_template(
        "staff.html", people=pagination.items, pagination=pagination,
        positions=active_positions(), spend=_spend_by_staff(),
        position_ids=position_ids, search=search, show_archived=show_archived,
        total_active=Staff.query.filter(Staff.is_active.is_(True)).count(),
    )


# ── Routes: a person ─────────────────────────────────────────────────────────


@staff_bp.route("/personnel/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, error = _read_staff_form(None)
        if error:
            return _render_staff_form(None, error)
        row = Staff(created_by=current_user.id, **data)
        db.session.add(row)
        db.session.flush()
        log_action("CREATE", "staff", resource_id=row.id,
                   detail="Added staff '%s'" % row.name)
        db.session.commit()
        flash("success|" + t["staff.created"])
        return modal_ok() if is_modal_request() else redirect(url_for("staff.index"))
    return _render_staff_form(None)


@staff_bp.route("/personnel/<int:pid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def edit(pid):
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    t = get_t()
    if request.method == "POST":
        data, error = _read_staff_form(row)
        if error:
            return _render_staff_form(row, error)
        for k, val in data.items():
            setattr(row, k, val)
        log_action("UPDATE", "staff", resource_id=row.id,
                   detail="Edited staff '%s'" % row.name)
        db.session.commit()
        flash("success|" + t["staff.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("staff.index"))
    return _render_staff_form(row)


@staff_bp.route("/personnel/<int:pid>/<any(archive,reactivate):what>",
                methods=["POST"])
@login_required
@require_perm("staff.edit")
def action(pid, what):
    """Someone who has left is archived: they leave the pickers, and the costs
    recorded against them stay named."""
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    row.is_active = what == "reactivate"
    log_action(what.upper(), "staff", resource_id=pid,
               detail="%s staff '%s'" % (what.title(), row.name))
    db.session.commit()
    flash("success|" + get_t()["staff.archived" if what == "archive"
                               else "staff.reactivated"])
    return redirect(request.referrer or url_for("staff.index"))


@staff_bp.route("/personnel/<int:pid>/supprimer", methods=["POST"])
@login_required
@require_perm("staff.delete")
def delete(pid):
    """Only for a row nothing has ever pointed at — a name mistyped, entered
    twice. Anyone with a cost against them is archived instead, so the ledger
    never ends up naming nobody."""
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    t = get_t()
    if Expense.query.filter_by(staff_id=row.id).count():
        flash("error|" + t["staff.err.delete_blocked"])
        return redirect(url_for("staff.index"))
    name = row.name
    db.session.delete(row)
    log_action("DELETE", "staff", resource_id=pid,
               detail="Deleted staff '%s'" % name)
    db.session.commit()
    flash("success|" + t["staff.deleted"])
    return redirect(request.referrer or url_for("staff.index"))


# ── Routes: the positions ────────────────────────────────────────────────────


def _positions_url():
    return url_for("staff.positions")


@staff_bp.route("/personnel/postes")
@login_required
@require_perm("staff.view")
def positions():
    """Kept off the Personnel list and behind a button, like the cashier's
    sites and accounts: the jobs are named once when the company is set up and
    then left alone, and a tab beside the people gave a once-a-year list the
    same standing as the one read every day."""
    # How many people hold each job, so one nothing points at can be told from
    # one in use -- and only that one may be deleted outright.
    held = {r[0]: int(r[1]) for r in db.session.query(
        Staff.position_id, db.func.count(Staff.id)).group_by(
        Staff.position_id).all()}
    return render_template(
        "staff_positions.html", held=held,
        positions=StaffPosition.query.order_by(
            StaffPosition.sort_order, StaffPosition.name).all())


def _save_position(row):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = StaffPosition.query.filter(
        db.func.lower(StaffPosition.name) == name.lower())
    if row:
        clash = clash.filter(StaffPosition.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(StaffPosition.sort_order)).scalar() or 0) + 1
        row = StaffPosition(sort_order=nxt)
        db.session.add(row)
    row.name = name
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "staff_position",
               resource_id=row.id,
               detail="%s position '%s'" % ("Created" if creating else "Updated", row.name))
    db.session.commit()
    return None


def _render_position_form(row, error=None):
    tpl = "_position_form.html" if is_modal_request() else "position_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


@staff_bp.route("/personnel/postes/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def position_new():
    if request.method == "POST":
        error = _save_position(None)
        if error:
            return _render_position_form(None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(_positions_url())
    return _render_position_form(None)


@staff_bp.route("/personnel/postes/<int:qid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def position_edit(qid):
    row = db.session.get(StaffPosition, qid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_position(row)
        if error:
            return _render_position_form(row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(_positions_url())
    return _render_position_form(row)


@staff_bp.route("/personnel/postes/<int:qid>/<any(archive,reactivate,delete):what>",
                methods=["POST"])
@login_required
@require_perm("staff.create")
def position_action(qid, what):
    row = db.session.get(StaffPosition, qid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if Staff.query.filter_by(position_id=row.id).count():
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(_positions_url())
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "staff_position", resource_id=qid,
                   detail="Deleted position '%s'" % name)
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "staff_position", resource_id=qid,
                   detail="%s position '%s'" % (what.title(), row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(_positions_url())
