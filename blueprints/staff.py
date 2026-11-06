"""RH -- the people, one file each.

Everybody who works on the ground exists once, here: an employee with the full
file (identity, position, CNSS, papers), or an external driver who works for a
subcontractor or a client and carries only what the pointing needs.

The rest points here rather than keeping a copy:
- a driver is a person plus the fleets he drives for (Operator rows, ticked on
  the file), so the Conducteurs page is only a view of these rows;
- an employee's login points here and takes its name and phone from the file;
- a cost names who it went out for (Expense.staff_id).

Past records (daily entries, services, fuel, costs) link the driver's file
(DriverLink), so a renamed person keeps his history; the name chosen then is
kept beside the link as a trace.

When the one list was made, people already typed into several places were
joined where the name was the same; the merge screen at the bottom of this
file is where somebody who knows decides the near-matches.
"""
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

import s3_storage
from payroll import active_allowances, payslip
from app import (current_user_fleet_ids, get_t, is_modal_request, log_action,
                 modal_ok, parse_amount, require_any_perm, require_perm)
from models import (Bank, CashMovement, DailyEntry, Department, DriverNameIgnored, Expense, Fleet, FuelMovement,
                    StaffContract, StaffSalary, StaffSalaryAllowance,
                    MaintenanceRecord, Operator, PersonMatchDismissed, Site, Staff,
                    StaffDocument, StaffPosition, User, Vehicle, db)

staff_bp = Blueprint("staff", __name__)

PER_PAGE = 50

KINDS = ("employee", "external")

# What the list's chips filter on.
SHOW = ("all", "employee", "external", "driver")

_UPLOAD_ERR_KEYS = {
    s3_storage.ERR_TOO_LARGE:      "photo.err.too_large",
    s3_storage.ERR_BAD_FORMAT:     "photo.err.bad_format",
    s3_storage.ERR_NOT_CONFIGURED: "photo.err.not_configured",
    s3_storage.ERR_S3:             "photo.err.s3",
    s3_storage.ERR_UNKNOWN:        "photo.err.unknown",
}

# Filled from the other file when two people are merged, if the kept one
# has nothing there.
_MERGE_FILL = ("first_name", "position_id", "phone", "matricule", "hired_on", "note",
               "sex", "birth_date", "birth_place", "nationality", "id_number",
               "photo_key", "address", "emergency_name", "emergency_phone",
               "site_id", "cnss_number", "company", "license_number",
               "license_categories", "license_expires")


# ── Helpers ──────────────────────────────────────────────────────────────────


def active_staff():
    """Who a cost can be attributed to. Someone archived keeps the costs
    already recorded against them, they are simply no longer offered."""
    return (Staff.query.filter(Staff.is_active.is_(True))
            .order_by(Staff.last_name, Staff.first_name).all())


def active_positions():
    return (StaffPosition.query.filter(StaffPosition.is_active.is_(True))
            .order_by(StaffPosition.sort_order, StaffPosition.name).all())


def active_departments():
    return (Department.query.filter(Department.is_active.is_(True))
            .order_by(Department.sort_order, Department.name).all())


def _active_sites():
    return (Site.query.filter(Site.is_active.is_(True))
            .order_by(Site.sort_order, Site.name).all())


def _accessible_fleets():
    """The fleets this user may tick on a file. A fleet he cannot see is left
    exactly as it is when he saves."""
    q = Fleet.query.filter(Fleet.is_active.is_(True)).order_by(Fleet.name)
    fids = current_user_fleet_ids()
    if fids is not None:
        q = q.filter(Fleet.id.in_(fids))
    return q.all()


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


# What the list can pick out about contracts: the ones ending within a month,
# the ones whose end date passed without being closed, nobody under contract.
CONTRACT_FILTERS = ("ending", "expired", "none")


def _contract_filter(key):
    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=30)).isoformat()
    open_ = StaffContract.ended_on.is_(None)
    if key == "ending":
        return Staff.contracts.any(db.and_(open_, StaffContract.end_date.isnot(None),
                                           StaffContract.end_date >= today,
                                           StaffContract.end_date <= soon))
    if key == "expired":
        return Staff.contracts.any(db.and_(open_, StaffContract.end_date < today))
    return ~Staff.contracts.any(open_)


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


def licence_state(person, today=None):
    """'expired', 'soon' (within 60 days) or None, for a driver's licence."""
    exp = person.license_expires
    if not exp or not _valid_date(exp):
        return None
    days = (datetime.strptime(exp, "%Y-%m-%d").date() - (today or date.today())).days
    if days < 0:
        return "expired"
    return "soon" if days <= 60 else None


def driver_from_form(raw):
    """What a driver dropdown sent, as (driver_id, name).

    The options carry the person's id: the record links his file and keeps
    his name as a trace. An old record whose name matches no file offers that
    name itself as an option; sent back, it stays a name with no link."""
    raw = (raw or "").strip()
    if not raw:
        return None, None
    if raw.isdigit():
        person = db.session.get(Staff, int(raw))
        if person is not None:
            return person.id, person.name
    return None, raw[:120]


# The four kinds of record that name a driver (DriverLink).
DRIVER_RECORDS = (DailyEntry, MaintenanceRecord, FuelMovement, Expense)


def unlinked_driver_names():
    """Driver names on records that link no file, with how many records each,
    leaving out the ones set aside on the merge screen."""
    counts = {}
    for model in DRIVER_RECORDS:
        for name, n in (db.session.query(model.operator_text, db.func.count(model.id))
                        .filter(model.driver_id.is_(None), model.operator_text.isnot(None))
                        .group_by(model.operator_text).all()):
            if name and name.strip():
                counts[name] = counts.get(name, 0) + int(n)
    ignored = {r.name for r in DriverNameIgnored.query.all()}
    return sorted(((n, c) for n, c in counts.items() if n not in ignored),
                  key=lambda x: (-x[1], x[0].lower()))


def link_driver_name(name, person):
    """Tie every record carrying this name, and no link yet, to the person."""
    total = 0
    for model in DRIVER_RECORDS:
        total += (model.query.filter(model.driver_id.is_(None), model.operator_text == name)
                  .update({model.driver_id: person.id}, synchronize_session=False))
    return total


def sync_login(person):
    """The login of an employee carries the name and phone of his file."""
    if person.login is not None:
        person.login.full_name = person.name[:120]
        person.login.phone = person.phone


def _apply_fleets(person, chosen):
    """Tick a fleet = he drives for it. Untick = no longer: the link goes if
    nothing was ever recorded under it, else it is archived so the vehicles
    and history pointing at it stay whole."""
    from blueprints.operators import operator_usage
    allowed = {f.id for f in _accessible_fleets()}
    have = {o.fleet_id: o for o in person.drives}
    for fid in allowed:
        link = have.get(fid)
        if fid in chosen:
            if link is None:
                db.session.add(Operator(fleet_id=fid, person=person, is_active=True,
                                        created_by=current_user.id))
            else:
                link.is_active = True
        elif link is not None:
            if link.id and operator_usage(link)["deletable"]:
                (Vehicle.query.filter_by(default_operator_id=link.id)
                 .update({Vehicle.default_operator_id: None}, synchronize_session=False))
                person.drives.remove(link)
                db.session.delete(link)
            else:
                link.is_active = False


def _upload_error(err):
    return get_t().get(_UPLOAD_ERR_KEYS.get(err, "photo.err.unknown"),
                       "Fichier non enregistré.")


def _apply_files(person):
    """Photo and new papers from the form. Returns an error string or None."""
    old_photo = person.photo_key
    if request.form.get("photo_remove") == "1":
        person.photo_key = None
    upload = request.files.get("photo")
    if upload and upload.filename:
        key, err = s3_storage.upload_person_photo(upload, person.name)
        if err:
            return _upload_error(err)
        person.photo_key = key
    for f in request.files.getlist("documents"):
        if not f or not f.filename:
            continue
        key, err = s3_storage.upload_person_document(f, person.name)
        if err:
            return _upload_error(err)
        person.documents.append(StaffDocument(
            name=f.filename[:160], file_key=key, content_type=f.mimetype,
            created_by=current_user.id))
    if old_photo and old_photo != person.photo_key:
        s3_storage.delete_photo(old_photo)
    return None


# ── The person's form ────────────────────────────────────────────────────────


def _txt(name, size):
    return (request.form.get(name) or "").strip()[:size] or None


def _name_taken(last_name, first_name, row):
    """Somebody else already has this surname and given name."""
    clash = Staff.query.filter(
        db.func.lower(Staff.last_name) == last_name.lower(),
        db.func.lower(db.func.coalesce(Staff.first_name, "")) == first_name.lower())
    if row:
        clash = clash.filter(Staff.id != row.id)
    return clash.first() is not None


MARITAL = ("celibataire", "marie", "divorce", "veuf")


def _identity_fields():
    sex = request.form.get("sex") or None
    marital = request.form.get("marital_status") or None
    children = request.form.get("children", type=int)
    return dict(sex=sex if sex in ("M", "F") else None,
                birth_date=_txt("birth_date", 10),
                birth_place=_txt("birth_place", 80),
                nationality=_txt("nationality", 60),
                id_number=_txt("id_number", 60),
                marital_status=marital if marital in MARITAL else None,
                children=children if children is not None and 0 <= children <= 30 else None)


def _contact_fields(employee):
    data = dict(phone=_txt("phone", 30))
    if employee:
        data.update(address=_txt("address", 200),
                    emergency_name=_txt("emergency_name", 120),
                    emergency_phone=_txt("emergency_phone", 30))
    return data


def _work_fields(row):
    """(data, None) or (None, error): an employee has a position -- an archived
    one only if it is the one he already had."""
    position_id = request.form.get("position_id", type=int) or None
    if not position_id or not StaffPosition.query.filter_by(
            id=position_id, is_active=True).first():
        if not (row and position_id and position_id == row.position_id):
            return None, get_t()["staff.err.position"]
    site_id = request.form.get("site_id", type=int) or None
    if site_id and not db.session.get(Site, site_id):
        site_id = None
    department_id = request.form.get("department_id", type=int) or None
    if department_id and not db.session.get(Department, department_id):
        department_id = None
    return dict(position_id=position_id, department_id=department_id, site_id=site_id,
                matricule=_txt("matricule", 40), hired_on=_txt("hired_on", 10),
                cnss_number=_txt("cnss_number", 40), category=_txt("category", 20),
                declared=request.form.get("declared") == "1"), None


def _driving_fields():
    return dict(license_number=_txt("license_number", 40),
                license_categories=_txt("license_categories", 40),
                license_expires=_txt("license_expires", 10))


def _digits(name, size):
    """Digits only (spaces dropped), or None; False when anything else was typed."""
    raw = (request.form.get(name) or "").replace(" ", "").strip()
    if not raw:
        return None
    return raw[:size] if raw.isdigit() else False


def _payment_fields():
    """(data, None) or (None, error): how the pay reaches the person. Only the
    parts of the chosen way are kept, so a file never holds a stale account."""
    t = get_t()
    method = request.form.get("pay_method") or None
    if method not in PAY_METHODS:
        method = None
    data = dict(pay_method=method, bank_id=None, bank_branch=None, bank_account=None,
                bank_key=None, mobile_money=None)
    if method == "transfer":
        bank_id = request.form.get("bank_id", type=int) or None
        if not bank_id or not db.session.get(Bank, bank_id):
            return None, t["pay.err.bank"]
        branch, account, key = _digits("bank_branch", 10), _digits("bank_account", 30), _digits("bank_key", 4)
        if branch is False or account is False or key is False:
            return None, t["pay.err.digits"]
        if not account:
            return None, t["pay.err.account"]
        data.update(bank_id=bank_id, bank_branch=branch, bank_account=account, bank_key=key)
    elif method == "mobile":
        number = _txt("mobile_money", 30)
        if not number:
            return None, t["pay.err.mobile"]
        data["mobile_money"] = number
    return data, None


def _ticked_fleets():
    chosen = set()
    for raw in request.form.getlist("fleets"):
        try:
            chosen.add(int(raw))
        except (TypeError, ValueError):
            pass
    return chosen


def _read_staff_form(row):
    """Returns (data, fleet ids ticked, None) or (None, None, error)."""
    t = get_t()
    kind = request.form.get("kind") or "employee"
    if kind not in KINDS:
        kind = "employee"
    last_name = (request.form.get("last_name") or "").strip()
    first_name = (request.form.get("first_name") or "").strip()
    if not last_name:
        return None, None, t["staff.err.name_required"]

    if _name_taken(last_name, first_name, row):
        return None, None, t["staff.err.name_taken"]

    if kind == "external" and row is not None and row.login is not None:
        return None, None, t["staff.err.external_login"]

    for field in ("birth_date", "hired_on", "license_expires"):
        v = (request.form.get(field) or "").strip()
        if v and not _valid_date(v):
            return None, None, t["staff.err.date"]

    data = dict(
        kind=kind,
        last_name=last_name[:80],
        first_name=first_name[:80] or None,
        phone=_txt("phone", 30),
        note=_txt("note", 255),
        **_driving_fields()
    )

    if kind == "external":
        data["company"] = _txt("company", 120)
    else:
        work, error = _work_fields(row)
        if error:
            return None, None, error
        data.update(work)
        data.update(_identity_fields())
        data.update(_contact_fields(True))

    return data, _ticked_fleets(), None


def _render_staff_form(row, error=None):
    tpl = "staff_form.html"
    status = 200
    driver_pos = StaffPosition.query.filter(
        db.func.lower(StaffPosition.name) == "chauffeur").first()
    if request.method == "POST":
        ticked = {int(x) for x in request.form.getlist("fleets") if x.isdigit()}
    elif row is not None:
        ticked = {o.fleet_id for o in row.drives if o.is_active}
    else:
        ticked = set()
    return render_template(
        tpl, row=row, error=error, positions=active_positions(),
        sites=_active_sites(), departments=active_departments(),
        fleets=_accessible_fleets(), ticked=ticked,
        driver_position_id=driver_pos.id if driver_pos else None,
        preset_kind=request.args.get("kind"),
        preset_driver=request.args.get("driver") == "1",
        today=date.today().isoformat()), status


def _back_url(row=None):
    """Where a saved file goes: where it was opened from, else the file."""
    nxt = request.args.get("next") or request.form.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for("staff.profile", pid=row.id) if row is not None else url_for("staff.index")


# ── Routes: the list ─────────────────────────────────────────────────────────


@staff_bp.route("/personnel")
@login_required
@require_perm("staff.view")
def index():
    show = request.args.get("show") if request.args.get("show") in SHOW else "all"
    position_ids = _ids("position")
    department_ids = _ids("department")
    contract = request.args.get("contract") if request.args.get("contract") in CONTRACT_FILTERS else ""
    search = (request.args.get("q") or "").strip()
    show_archived = request.args.get("archived") == "1"

    q = Staff.query
    if not show_archived:
        q = q.filter(Staff.is_active.is_(True))
    if show in ("employee", "external"):
        q = q.filter(Staff.kind == show)
    elif show == "driver":
        q = q.filter(Staff.drives.any(Operator.is_active.is_(True)))
    if position_ids:
        q = q.filter(Staff.position_id.in_(position_ids))
    if department_ids:
        q = q.filter(Staff.department_id.in_(department_ids))
    if contract:
        q = q.filter(Staff.kind == "employee", _contract_filter(contract))
    if search:
        like = "%" + search + "%"
        q = q.filter(db.or_(Staff.last_name.ilike(like), Staff.first_name.ilike(like),
                            Staff.matricule.ilike(like), Staff.phone.ilike(like),
                            Staff.company.ilike(like)))

    pagination = q.order_by(Staff.last_name, Staff.first_name).paginate(
        page=request.args.get("page", 1, type=int), per_page=PER_PAGE,
        error_out=False)

    return render_template(
        "staff.html", people=pagination.items, pagination=pagination,
        positions=active_positions(), departments=active_departments(),
        spend=_spend_by_staff(), show=show,
        position_ids=position_ids, department_ids=department_ids, contract=contract,
        search=search, show_archived=show_archived,
        total_active=Staff.query.filter(Staff.is_active.is_(True),
                                        Staff.kind == "employee").count(),
    )


# ── Routes: a person ─────────────────────────────────────────────────────────


@staff_bp.route("/personnel/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def new():
    t = get_t()
    if request.method == "POST":
        data, chosen, error = _read_staff_form(None)
        if error:
            return _render_staff_form(None, error)
        row = Staff(created_by=current_user.id, **data)
        db.session.add(row)
        db.session.flush()
        _apply_fleets(row, chosen)
        ferr = _apply_files(row)
        if ferr:
            db.session.rollback()
            return _render_staff_form(None, ferr)
        log_action("CREATE", "staff", resource_id=row.id,
                   detail="Added %s '%s'" % (row.kind, row.name))
        db.session.commit()
        flash("success|" + t["staff.created"])
        return redirect(_back_url(row))
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
        data, chosen, error = _read_staff_form(row)
        if error:
            return _render_staff_form(row, error)
        old_name = row.name
        for k, val in data.items():
            setattr(row, k, val)
        _apply_fleets(row, chosen)
        gone = []
        for doc_id in request.form.getlist("doc_remove"):
            doc = db.session.get(StaffDocument, int(doc_id)) if doc_id.isdigit() else None
            if doc and doc.staff_id == row.id:
                gone.append(doc.file_key)
                row.documents.remove(doc)
        ferr = _apply_files(row)
        if ferr:
            db.session.rollback()
            return _render_staff_form(db.session.get(Staff, pid), ferr)
        sync_login(row)
        log_action("UPDATE", "staff", resource_id=row.id,
                   detail="Edited %s '%s'" % (row.kind, row.name)
                   + (" (was '%s')" % old_name if old_name != row.name else ""))
        db.session.commit()
        # The files go once the database no longer points at them.
        for key in gone:
            s3_storage.delete_photo(key)
        flash("success|" + t["staff.updated"])
        return redirect(_back_url(row))
    return _render_staff_form(row)


# ── Routes: one part of the file at a time ───────────────────────────────────
#
# Each part of the file (the head, identity, work, contact, driving, note)
# has its own small form: it reads and saves its own fields and nothing else.
# The full page form is for a new person only.

SECTIONS = ("head", "identity", "work", "payment", "contact", "driving", "note")
# The parts only an employee's file has.
EMPLOYEE_SECTIONS = ("identity", "work", "payment")
PAY_METHODS = ("transfer", "mobile", "cash")
_SECTION_DATES = {"identity": ("birth_date",), "work": ("hired_on",), "driving": ("license_expires",)}


def _read_section(row, section):
    """(data, None) or (None, error) for one part of the file."""
    t = get_t()
    for field in _SECTION_DATES.get(section, ()):
        v = (request.form.get(field) or "").strip()
        if v and not _valid_date(v):
            return None, t["staff.err.date"]
    if section == "head":
        kind = request.form.get("kind") if request.form.get("kind") in KINDS else row.kind
        last_name = (request.form.get("last_name") or "").strip()
        first_name = (request.form.get("first_name") or "").strip()
        if not last_name:
            return None, t["staff.err.name_required"]
        if _name_taken(last_name, first_name, row):
            return None, t["staff.err.name_taken"]
        if kind == "external" and row.login is not None:
            return None, t["staff.err.external_login"]
        data = dict(kind=kind, last_name=last_name[:80], first_name=first_name[:80] or None)
        if kind == "external":
            data["company"] = _txt("company", 120)
        return data, None
    if section == "identity":
        return _identity_fields(), None
    if section == "work":
        return _work_fields(row)
    if section == "payment":
        return _payment_fields()
    if section == "contact":
        return _contact_fields(row.is_employee), None
    if section == "driving":
        return _driving_fields(), None
    return dict(note=_txt("note", 255)), None


def _render_section_form(row, section, error=None):
    tpl = "_person_section_form.html" if is_modal_request() else "person_section_form.html"
    status = 422 if (error and is_modal_request()) else 200
    driver_pos = StaffPosition.query.filter(
        db.func.lower(StaffPosition.name) == "chauffeur").first()
    if request.method == "POST":
        ticked = _ticked_fleets()
    else:
        ticked = {o.fleet_id for o in row.drives if o.is_active}
    return render_template(
        tpl, row=row, section=section, error=error, positions=active_positions(),
        sites=_active_sites(), departments=active_departments(),
        fleets=_accessible_fleets(), ticked=ticked,
        banks=Bank.query.filter_by(is_active=True).order_by(Bank.sort_order, Bank.name).all(),
        driver_position_id=driver_pos.id if driver_pos else None,
        today=date.today().isoformat()), status


@staff_bp.route("/personnel/<int:pid>/modifier/<section>", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def edit_section(pid, section):
    row = db.session.get(Staff, pid)
    if not row or section not in SECTIONS:
        abort(404)
    if section in EMPLOYEE_SECTIONS and not row.is_employee:
        abort(404)
    if request.method == "POST":
        data, error = _read_section(row, section)
        if error:
            return _render_section_form(row, section, error)
        old_name = row.name
        for k, val in data.items():
            setattr(row, k, val)
        if section == "driving":
            _apply_fleets(row, _ticked_fleets())
        if section == "head":
            ferr = _apply_files(row)
            if ferr:
                db.session.rollback()
                return _render_section_form(db.session.get(Staff, pid), section, ferr)
        sync_login(row)
        log_action("UPDATE", "staff", resource_id=row.id,
                   detail="Edited %s of '%s'" % (section, row.name)
                   + (" (was '%s')" % old_name if old_name != row.name else ""))
        db.session.commit()
        flash("success|" + get_t()["staff.updated"])
        return modal_ok() if is_modal_request() else redirect(url_for("staff.profile", pid=row.id))
    return _render_section_form(row, section)


@staff_bp.route("/personnel/<int:pid>/documents/ajouter", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def document_add(pid):
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    error = None
    if request.method == "POST":
        if not any(f and f.filename for f in request.files.getlist("documents")):
            error = get_t()["staff.err.no_document"]
        else:
            error = _apply_files(row)
            if error:
                db.session.rollback()
            else:
                log_action("UPDATE", "staff", resource_id=row.id,
                           detail="Added papers to '%s'" % row.name)
                db.session.commit()
                flash("success|" + get_t()["staff.updated"])
                return modal_ok() if is_modal_request() else redirect(
                    url_for("staff.profile", pid=row.id, tab="documents"))
    tpl = "_person_documents_form.html" if is_modal_request() else "person_documents_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, row=row, error=error), status


@staff_bp.route("/personnel/document/<int:did>/supprimer", methods=["POST"])
@login_required
@require_perm("staff.edit")
def document_delete(did):
    doc = db.session.get(StaffDocument, did)
    if not doc:
        abort(404)
    pid, key, name = doc.staff_id, doc.file_key, doc.name
    log_action("UPDATE", "staff", resource_id=pid, detail="Removed paper '%s'" % name)
    db.session.delete(doc)
    db.session.commit()
    s3_storage.delete_photo(key)
    flash("success|" + get_t()["staff.updated"])
    return redirect(url_for("staff.profile", pid=pid, tab="documents"))


def _years_since(ymd, today=None):
    """Whole years from a YYYY-MM-DD date to today, or None."""
    if not ymd or not _valid_date(ymd):
        return None
    d = datetime.strptime(ymd, "%Y-%m-%d").date()
    today = today or date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def _salary_history(person):
    """The salary tab: each line with its change from the one before (newest
    first), and the points of the progression chart (oldest first, carried on
    to today so the last step shows)."""
    today = date.today().isoformat()
    lines = list(reversed(person.salaries))
    rows, prev = [], None
    for s in lines:
        change = s.gross - prev.gross if prev is not None else None
        pct = round(change * 100.0 / prev.gross, 1) if change is not None and prev.gross else None
        rows.append({"line": s, "change": change, "pct": pct, "state": s.state()})
        prev = s
    rows.reverse()
    points = [{"x": s.effective_on, "y": s.gross} for s in lines]
    if len(points) >= 2:
        points.append({"x": max(today, lines[-1].effective_on), "y": lines[-1].gross})
    return rows, {"points": points if len(lines) >= 2 else []}


def _month_label(year, month):
    from app import MONTH_NAMES, current_lang
    names = MONTH_NAMES.get(current_lang(), MONTH_NAMES["en"])
    return "%s %d" % (names[month - 1].capitalize(), year)


def _payslip_for_tab(person):
    """The payslip of the month asked (?mois=YYYY-MM, this month by default),
    the error when there is none, and the months to pick from: back to the
    first salary line, at most two years."""
    today = date.today()
    raw = request.args.get("mois") or ""
    try:
        year, month = int(raw[:4]), int(raw[5:7])
        if not 1 <= month <= 12:
            raise ValueError
    except ValueError:
        year, month = today.year, today.month
    first = person.salaries[-1].effective_on[:7] if person.salaries else today.strftime("%Y-%m")
    options, y, m = [], today.year, today.month
    for _ in range(24):
        key = "%04d-%02d" % (y, m)
        if key < first:
            break
        options.append((url_for("staff.profile", pid=person.id, tab="payslip", mois=key),
                        _month_label(y, m), (y, m) == (year, month)))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    slip, err = payslip(person, year, month)
    if slip is not None:
        slip["label"] = _month_label(year, month)
    return slip, (get_t()[err] if err else None), options


@staff_bp.route("/personnel/<int:pid>/bulletin/<int:year>-<int:month>")
@login_required
@require_perm("staff.view")
def payslip_print(pid, year, month):
    """The payslip alone on its page, to print or save as PDF."""
    person = db.session.get(Staff, pid)
    if not person or not 1 <= month <= 12:
        abort(404)
    slip, err = payslip(person, year, month)
    if slip is None:
        abort(404)
    slip["label"] = _month_label(year, month)
    from blueprints.invoicing import invoice_settings
    return render_template("payslip_print.html", slip=slip, co=invoice_settings())


def _company():
    from blueprints.invoicing import invoice_settings
    return invoice_settings()


def _salary_columns(person):
    """The allowances that appear on any of the person's lines, one column each."""
    seen = {}
    for s in person.salaries:
        for a in s.allowances:
            seen[a.allowance_id] = a.allowance
    return sorted(seen.values(), key=lambda a: (a.sort_order, a.name))


@staff_bp.route("/personnel/<int:pid>")
@login_required
@require_perm("staff.view")
def profile(pid):
    """The RH door to a person: who they are and on what terms they work.
    What they do on the ground is the other door (operators.person)."""
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    # The cash box's costs for a person stay in the cash box: the RH side does
    # not show them for now.
    tabs = (["file", "contracts", "salary"] + (["payslip"] if row.declared else []) + ["documents"]
            if row.is_employee else ["file", "documents"])
    tab = request.args.get("tab") if request.args.get("tab") in tabs else "file"
    salary_rows = salary_chart = salary_cols = advances = None
    if tab == "salary":
        salary_rows, salary_chart = _salary_history(row)
        salary_cols = _salary_columns(row)
        # What the cash box handed him against his salary -- advances and
        # bonuses, never its costs.
        advances = [(a, _month_label(int(a.advance_month[:4]), int(a.advance_month[5:])))
                    for a in CashMovement.query.filter(CashMovement.kind.in_(("avance", "prime")),
                                                       CashMovement.staff_id == row.id)
                    .order_by(CashMovement.date.desc(), CashMovement.id.desc()).all()]
    slip = slip_error = month_options = None
    if tab == "payslip":
        slip, slip_error, month_options = _payslip_for_tab(row)
    return render_template(
        "staff_profile.html", p=row, tabs=tabs, tab=tab,
        salary_rows=salary_rows, salary_chart=salary_chart, salary_cols=salary_cols,
        advances=advances,
        slip=slip, slip_error=slip_error, month_options=month_options,
        co=_company() if tab == "payslip" else None,
        age=_years_since(row.birth_date), seniority=_years_since(row.hired_on),
        licence=licence_state(row))


@staff_bp.route("/personnel/document/<int:did>")
@login_required
@require_any_perm("staff.view", "operator.view")
def document(did):
    doc = db.session.get(StaffDocument, did)
    if not doc:
        abort(404)
    url = s3_storage.signed_url(doc.file_key)
    if not url:
        abort(404)
    return redirect(url)


@staff_bp.route("/personnel/<int:pid>/<any(archive,reactivate):what>",
                methods=["POST"])
@login_required
@require_perm("staff.edit")
def action(pid, what):
    """Someone who has left is archived: they leave every picker, and what was
    recorded against them stays named."""
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


def _delete_blockers(row):
    """Why a person cannot be deleted outright (then he is archived)."""
    from blueprints.operators import operator_usage
    out = []
    if Expense.query.filter_by(staff_id=row.id).count():
        out.append("expenses")
    if row.login is not None:
        out.append("login")
    if any(not operator_usage(o)["deletable"] for o in row.drives) or any(
            m.query.filter_by(driver_id=row.id).first() for m in DRIVER_RECORDS):
        out.append("driving")
    return out


@staff_bp.route("/personnel/<int:pid>/supprimer", methods=["POST"])
@login_required
@require_perm("staff.delete")
def delete(pid):
    """Only for a file nothing has ever pointed at -- a name mistyped, entered
    twice. Anyone with history is archived instead."""
    row = db.session.get(Staff, pid)
    if not row:
        abort(404)
    t = get_t()
    if _delete_blockers(row):
        flash("error|" + t["staff.err.delete_blocked"])
        return redirect(url_for("staff.index"))
    name = row.name
    for link in list(row.drives):
        # A deleted (tombstoned) machine may still name him as default driver.
        (Vehicle.query.filter_by(default_operator_id=link.id)
         .update({Vehicle.default_operator_id: None}, synchronize_session=False))
        db.session.delete(link)
    gone = [d.file_key for d in row.documents] + ([row.photo_key] if row.photo_key else [])
    PersonMatchDismissed.query.filter(db.or_(
        PersonMatchDismissed.staff_a_id == row.id,
        PersonMatchDismissed.staff_b_id == row.id)).delete(synchronize_session=False)
    db.session.delete(row)
    log_action("DELETE", "staff", resource_id=pid, detail="Deleted staff '%s'" % name)
    db.session.commit()
    for key in gone:
        s3_storage.delete_photo(key)
    flash("success|" + t["staff.deleted"])
    return redirect(request.referrer or url_for("staff.index"))


# ── Routes: contracts ────────────────────────────────────────────────────────
#
# One in force at a time. A new one -- a renewal, a raise -- closes the one
# before it the day before it starts, so the person's list of contracts is the
# history of his terms. Ending one early says when and why.


def _get_contract_or_404(cid):
    c = db.session.get(StaffContract, cid)
    if not c:
        abort(404)
    return c


def _day_before(ymd):
    return (datetime.strptime(ymd, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()


def _day_after(ymd):
    return (datetime.strptime(ymd, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()


def _read_contract_form(person, contract):
    """Returns (data, None) or (None, error)."""
    t = get_t()
    kind = request.form.get("kind")
    if kind not in StaffContract.KINDS:
        return None, t["contract.err.kind"]
    start = (request.form.get("start_date") or "").strip()
    if not _valid_date(start):
        return None, t["contract.err.start"]
    end = (request.form.get("end_date") or "").strip() or None
    if kind in StaffContract.DATED and not end:
        return None, t["contract.err.end_required"]
    if end and (not _valid_date(end) or end < start):
        return None, t["contract.err.end"]
    trial = (request.form.get("trial_end") or "").strip() or None
    if trial and (not _valid_date(trial) or trial < start):
        return None, t["contract.err.trial"]
    # A new contract follows the last one; it cannot start before it.
    if contract is None and person.contracts:
        last = person.contracts[0]
        if start <= last.start_date:
            return None, t["contract.err.overlap"] % _fmt(last.start_date)
    # The salary line given with this contract moves with its start, unless
    # another line already sits on the new day.
    if contract is not None and start != contract.start_date:
        clash = StaffSalary.query.filter(StaffSalary.staff_id == person.id,
                                         StaffSalary.effective_on == start,
                                         StaffSalary.contract_id != contract.id).first()
        if clash is not None and any(s.contract_id == contract.id for s in person.salaries):
            return None, t["salary.err.day_taken"] % _fmt(start)
    return dict(kind=kind, start_date=start, end_date=end if kind != "cdi" else None,
                trial_end=trial,
                note=(request.form.get("note") or "").strip()[:255] or None), None


def _fmt(ymd):
    return "%s/%s/%s" % (ymd[8:], ymd[5:7], ymd[:4])


def _save_contract_file(contract):
    """The signed paper, when one is sent. Returns an error string or None."""
    old = contract.file_key
    if request.form.get("file_remove") == "1":
        contract.file_key = contract.file_type = None
    f = request.files.get("file")
    if f and f.filename:
        key, err = s3_storage.upload_person_document(f, contract.person.name)
        if err:
            return _upload_error(err)
        contract.file_key, contract.file_type = key, f.mimetype
    if old and old != contract.file_key:
        s3_storage.delete_photo(old)
    return None


def _render_contract_form(person, contract, error=None, preset=None):
    tpl = "_contract_form.html" if is_modal_request() else "contract_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, p=person, c=contract, error=error, preset=preset or {},
                           kinds=StaffContract.KINDS), status


@staff_bp.route("/personnel/<int:pid>/contrats/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def contract_new(pid):
    person = db.session.get(Staff, pid)
    if not person or not person.is_employee:
        abort(404)
    t = get_t()
    # The new one follows the last contract entered, even one not started yet.
    cur = person.contracts[0] if person.contracts else None
    # Renewing: the same terms, starting the day after it ends.
    preset = {}
    if request.args.get("renew") and cur is not None:
        start = _day_after(cur.end_date) if cur.end_date else date.today().isoformat()
        paid = person.salary_on(start)
        preset = {"kind": cur.kind, "start_date": start,
                  "base_salary": paid.amount if paid is not None else None}
        # A fixed-term contract renewed for as long as the last one ran.
        if cur.end_date:
            length = (datetime.strptime(cur.end_date, "%Y-%m-%d")
                      - datetime.strptime(cur.start_date, "%Y-%m-%d"))
            preset["end_date"] = (datetime.strptime(start, "%Y-%m-%d") + length).strftime("%Y-%m-%d")
    if request.method == "POST":
        data, error = _read_contract_form(person, None)
        salary, serr = _read_amount("base_salary", required=False)
        error = error or serr
        if error:
            return _render_contract_form(person, None, error, preset)
        c = StaffContract(staff_id=person.id, created_by=current_user.id, **data)
        db.session.add(c)
        db.session.flush()
        db.session.refresh(person)
        ferr = _save_contract_file(c)
        if ferr:
            db.session.rollback()
            return _render_contract_form(person, None, ferr, preset)
        _salary_with_contract(person, c, salary)
        if cur is not None and not cur.ended_on:
            cur.ended_on = _day_before(c.start_date)
            cur.end_reason = "renouvele"
        log_action("CREATE", "staff_contract", resource_id=c.id,
                   detail="Contract %s from %s for '%s'" % (c.kind, c.start_date, person.name))
        db.session.commit()
        flash("success|" + t["contract.created"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("staff.profile", pid=person.id, tab="contracts"))
    return _render_contract_form(person, None, preset=preset)


@staff_bp.route("/personnel/contrats/<int:cid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def contract_edit(cid):
    c = _get_contract_or_404(cid)
    t = get_t()
    if request.method == "POST":
        data, error = _read_contract_form(c.person, c)
        if error:
            return _render_contract_form(c.person, c, error)
        for s in c.person.salaries:
            if s.contract_id == c.id:
                s.effective_on = data["start_date"]
        # The contract this one replaced ends the day before it starts: moving
        # the start moves that end too, so the two never leave a gap or overlap.
        if data["start_date"] != c.start_date:
            before = (StaffContract.query.filter(StaffContract.staff_id == c.staff_id,
                                                 StaffContract.id != c.id,
                                                 StaffContract.end_reason == "renouvele",
                                                 StaffContract.ended_on == _day_before(c.start_date))
                      .first())
            if before is not None:
                before.ended_on = _day_before(data["start_date"])
        for k, v in data.items():
            setattr(c, k, v)
        ferr = _save_contract_file(c)
        if ferr:
            db.session.rollback()
            return _render_contract_form(c.person, _get_contract_or_404(cid), ferr)
        log_action("UPDATE", "staff_contract", resource_id=c.id,
                   detail="Edited contract %s from %s of '%s'" % (c.kind, c.start_date, c.person.name))
        db.session.commit()
        flash("success|" + t["contract.updated"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("staff.profile", pid=c.staff_id, tab="contracts"))
    return _render_contract_form(c.person, c)


@staff_bp.route("/personnel/contrats/<int:cid>/terminer", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def contract_end(cid):
    """When and why a contract stopped: on its planned end, or early."""
    c = _get_contract_or_404(cid)
    t = get_t()
    error = None
    if request.method == "POST":
        ended = (request.form.get("ended_on") or "").strip()
        reason = request.form.get("end_reason")
        if not _valid_date(ended) or ended < c.start_date:
            error = t["contract.err.ended_on"]
        elif reason not in StaffContract.END_REASONS:
            error = t["contract.err.reason"]
        else:
            c.ended_on, c.end_reason = ended, reason
            log_action("UPDATE", "staff_contract", resource_id=c.id,
                       detail="Ended contract of '%s' on %s (%s)" % (c.person.name, ended, reason))
            db.session.commit()
            flash("success|" + t["contract.ended"])
            return modal_ok() if is_modal_request() else redirect(
                url_for("staff.profile", pid=c.staff_id, tab="contracts"))
    tpl = "_contract_end_form.html" if is_modal_request() else "contract_end_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, c=c, error=error, reasons=StaffContract.END_REASONS,
                           today=date.today().isoformat()), status


@staff_bp.route("/personnel/contrats/<int:cid>/supprimer", methods=["POST"])
@login_required
@require_perm("staff.delete")
def contract_delete(cid):
    """For a contract typed by mistake. The one it closed opens again, and the
    salary line given with it goes too."""
    c = _get_contract_or_404(cid)
    pid, key = c.staff_id, c.file_key
    StaffSalary.query.filter_by(contract_id=c.id).delete()
    before = (StaffContract.query.filter(StaffContract.staff_id == pid,
                                         StaffContract.id != c.id,
                                         StaffContract.end_reason == "renouvele",
                                         StaffContract.ended_on == _day_before(c.start_date))
              .first())
    if before is not None:
        before.ended_on = before.end_reason = None
    log_action("DELETE", "staff_contract", resource_id=cid,
               detail="Deleted contract %s from %s of '%s'" % (c.kind, c.start_date, c.person.name))
    db.session.delete(c)
    db.session.commit()
    if key:
        s3_storage.delete_photo(key)
    flash("success|" + get_t()["contract.deleted"])
    return redirect(url_for("staff.profile", pid=pid, tab="contracts"))


@staff_bp.route("/personnel/contrats/<int:cid>/document")
@login_required
@require_perm("staff.view")
def contract_file(cid):
    c = _get_contract_or_404(cid)
    url = s3_storage.signed_url(c.file_key) if c.file_key else None
    if not url:
        abort(404)
    return redirect(url)


# ── Routes: the salary ───────────────────────────────────────────────────────
#
# A dated history: each line says "from this day, the base salary is this
# much". A raise is a new line dated when it applies, never an edit of the old
# figure, so the months behind it keep the salary they were paid on.


def _read_amount(field, required=True):
    """(amount, None) or (None, error); (None, None) when left empty and not
    required."""
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return None, (get_t()["salary.err.amount"] if required else None)
    amount = parse_amount(raw)
    if amount is None or amount <= 0:
        return None, get_t()["salary.err.amount"]
    return amount, None


def _salary_with_contract(person, contract, amount):
    """The salary typed on a new contract's form: a line on its first day,
    unless it is the salary already in force then."""
    if amount is None:
        return
    same_day = next((s for s in person.salaries if s.effective_on == contract.start_date), None)
    if same_day is not None:
        same_day.amount, same_day.contract_id = amount, contract.id
        return
    before = person.salary_on(contract.start_date)
    if before is not None and before.amount == amount:
        return
    line = StaffSalary(
        staff_id=person.id, effective_on=contract.start_date, amount=amount,
        reason="renouvellement" if person.salaries else "embauche",
        contract_id=contract.id, created_by=current_user.id)
    # The allowances go on as they were: only the base salary was typed.
    if before is not None:
        for a in before.allowances:
            line.allowances.append(StaffSalaryAllowance(allowance_id=a.allowance_id, amount=a.amount))
    db.session.add(line)


def _get_salary_or_404(sid):
    s = db.session.get(StaffSalary, sid)
    if not s:
        abort(404)
    return s


def _first_of_next_month():
    d = date.today().replace(day=1) + timedelta(days=32)
    return d.replace(day=1).isoformat()


def _salary_form(person, line):
    """Saves the form into a line (a new one when line is None). Returns the
    error, or None once saved."""
    t = get_t()
    day = (request.form.get("effective_on") or "").strip()
    if not _valid_date(day):
        return t["salary.err.day"]
    amount, error = _read_amount("amount")
    if error:
        return error
    reason = request.form.get("reason")
    if reason not in StaffSalary.REASONS:
        return t["salary.err.reason"]
    clash = next((s for s in person.salaries
                  if s.effective_on == day and (line is None or s.id != line.id)), None)
    if clash is not None:
        return t["salary.err.day_taken"] % _fmt(day)
    # Each allowance in use: an amount, or nothing.
    allowances = active_allowances()
    amounts = {}
    for a in allowances:
        raw = (request.form.get("allow_%d" % a.id) or "").strip()
        if not raw:
            continue
        value = parse_amount(raw)
        if value is None or value < 0:
            return t["salary.err.allowance"] % a.name
        if value:
            amounts[a.id] = value
    if line is None:
        line = StaffSalary(staff_id=person.id, created_by=current_user.id)
        db.session.add(line)
    line.effective_on, line.amount, line.reason = day, amount, reason
    line.note = (request.form.get("note") or "").strip()[:255] or None
    # An allowance no longer in use is left as it was on the line.
    have = {x.allowance_id: x for x in line.allowances}
    for a in allowances:
        if a.id in amounts:
            if a.id in have:
                have[a.id].amount = amounts[a.id]
            else:
                line.allowances.append(StaffSalaryAllowance(allowance_id=a.id, amount=amounts[a.id]))
        elif a.id in have:
            line.allowances.remove(have[a.id])
    return None


def _render_salary_form(person, line, error=None):
    tpl = "_salary_form.html" if is_modal_request() else "salary_form.html"
    status = 422 if (error and is_modal_request()) else 200
    # A new line starts from the last one: only what moves is changed.
    last = person.salaries[0] if person.salaries else None
    preset = {"effective_on": _first_of_next_month(),
              "reason": "augmentation" if person.salaries else "embauche",
              "amount": last.amount if last is not None else None}
    source = line if line is not None else last
    allow_values = {a.allowance_id: a.amount for a in source.allowances} if source is not None else {}
    return render_template(tpl, p=person, s=line, error=error, preset=preset,
                           reasons=StaffSalary.REASONS, allowances=active_allowances(),
                           allow_values=allow_values), status


@staff_bp.route("/personnel/<int:pid>/salaire/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def salary_new(pid):
    person = db.session.get(Staff, pid)
    if not person or not person.is_employee:
        abort(404)
    if request.method == "POST":
        error = _salary_form(person, None)
        if error:
            return _render_salary_form(person, None, error)
        log_action("CREATE", "staff_salary", resource_id=person.id,
                   detail="Salary of '%s' from %s: %s" % (person.name, request.form.get("effective_on"),
                                                           request.form.get("amount")))
        db.session.commit()
        flash("success|" + get_t()["salary.created"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("staff.profile", pid=person.id, tab="salary"))
    return _render_salary_form(person, None)


@staff_bp.route("/personnel/salaire/<int:sid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.edit")
def salary_edit(sid):
    """To correct a line typed wrong -- a raise is a new line, not this."""
    line = _get_salary_or_404(sid)
    if request.method == "POST":
        error = _salary_form(line.person, line)
        if error:
            return _render_salary_form(line.person, line, error)
        log_action("UPDATE", "staff_salary", resource_id=line.id,
                   detail="Edited salary of '%s' from %s: %s" % (line.person.name, line.effective_on, line.amount))
        db.session.commit()
        flash("success|" + get_t()["salary.updated"])
        return modal_ok() if is_modal_request() else redirect(
            url_for("staff.profile", pid=line.staff_id, tab="salary"))
    return _render_salary_form(line.person, line)


@staff_bp.route("/personnel/salaire/<int:sid>/supprimer", methods=["POST"])
@login_required
@require_perm("staff.delete")
def salary_delete(sid):
    line = _get_salary_or_404(sid)
    pid = line.staff_id
    log_action("DELETE", "staff_salary", resource_id=sid,
               detail="Deleted salary of '%s' from %s: %s" % (line.person.name, line.effective_on, line.amount))
    db.session.delete(line)
    db.session.commit()
    flash("success|" + get_t()["salary.deleted"])
    return redirect(url_for("staff.profile", pid=pid, tab="salary"))


# ── Routes: the positions and the departments ────────────────────────────────
#
# Two short lists kept by hand, the same way: a name each, archived rather
# than deleted once somebody holds it. `kind` says which ("position" or
# "department"): the model, the column on the person that points at it, and
# the endpoints all follow from it.


def _list_model(kind):
    return {"position": (StaffPosition, Staff.position_id),
            "department": (Department, Staff.department_id)}[kind]


def _list_url(kind):
    return url_for("staff.positions" if kind == "position" else "staff.departments")


def _list_page(kind):
    model, col = _list_model(kind)
    # How many people hold each one, so one nothing points at can be told from
    # one in use -- and only that one may be deleted outright.
    held = {r[0]: int(r[1]) for r in db.session.query(col, db.func.count(Staff.id)).filter(
        Staff.is_active.is_(True)).group_by(col).all()}
    return render_template(
        "staff_positions.html", kind=kind, held=held,
        rows=model.query.order_by(model.sort_order, model.name).all())


@staff_bp.route("/personnel/postes")
@login_required
@require_perm("staff.view")
def positions():
    return _list_page("position")


@staff_bp.route("/personnel/departements")
@login_required
@require_perm("staff.view")
def departments():
    return _list_page("department")


def _save_list_row(kind, row):
    t = get_t()
    model, _ = _list_model(kind)
    name = (request.form.get("name") or "").strip()
    if not name:
        return t.get("list.err.name_required", "Le nom est obligatoire.")
    clash = model.query.filter(db.func.lower(model.name) == name.lower())
    if row:
        clash = clash.filter(model.id != row.id)
    if clash.first():
        return t.get("list.err.name_taken", "Ce nom existe déjà.")
    creating = row is None
    if creating:
        nxt = (db.session.query(db.func.max(model.sort_order)).scalar() or 0) + 1
        row = model(sort_order=nxt)
        db.session.add(row)
    row.name = name[:80]
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "staff_" + kind, resource_id=row.id,
               detail="%s %s '%s'" % ("Created" if creating else "Updated", kind, row.name))
    db.session.commit()
    return None


def _render_list_form(kind, row, error=None):
    tpl = "_position_form.html" if is_modal_request() else "position_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, kind=kind, row=row, error=error), status


def _list_new(kind):
    if request.method == "POST":
        error = _save_list_row(kind, None)
        if error:
            return _render_list_form(kind, None, error)
        flash("success|" + get_t().get("list.created", "Ajouté."))
        return modal_ok() if is_modal_request() else redirect(_list_url(kind))
    return _render_list_form(kind, None)


def _list_edit(kind, qid):
    model, _ = _list_model(kind)
    row = db.session.get(model, qid)
    if not row:
        abort(404)
    if request.method == "POST":
        error = _save_list_row(kind, row)
        if error:
            return _render_list_form(kind, row, error)
        flash("success|" + get_t().get("list.updated", "Modifié."))
        return modal_ok() if is_modal_request() else redirect(_list_url(kind))
    return _render_list_form(kind, row)


def _list_action(kind, qid, what):
    model, col = _list_model(kind)
    row = db.session.get(model, qid)
    if not row:
        abort(404)
    t = get_t()
    if what == "delete":
        if Staff.query.filter(col == row.id).count():
            flash("error|" + t.get("list.err.delete_blocked",
                                   "Impossible de supprimer : cet élément est "
                                   "utilisé. Archivez-le à la place."))
            return redirect(_list_url(kind))
        name = row.name
        db.session.delete(row)
        log_action("DELETE", "staff_" + kind, resource_id=qid,
                   detail="Deleted %s '%s'" % (kind, name))
        msg = "list.deleted"
    else:
        row.is_active = what == "reactivate"
        log_action(what.upper(), "staff_" + kind, resource_id=qid,
                   detail="%s %s '%s'" % (what.title(), kind, row.name))
        msg = "list.archived" if what == "archive" else "list.reactivated"
    db.session.commit()
    flash("success|" + t.get(msg, "Fait."))
    return redirect(_list_url(kind))


@staff_bp.route("/personnel/postes/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def position_new():
    return _list_new("position")


@staff_bp.route("/personnel/postes/<int:qid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def position_edit(qid):
    return _list_edit("position", qid)


@staff_bp.route("/personnel/postes/<int:qid>/<any(archive,reactivate,delete):what>",
                methods=["POST"])
@login_required
@require_perm("staff.create")
def position_action(qid, what):
    return _list_action("position", qid, what)


@staff_bp.route("/personnel/departements/nouveau", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def department_new():
    return _list_new("department")


@staff_bp.route("/personnel/departements/<int:qid>/modifier", methods=["GET", "POST"])
@login_required
@require_perm("staff.create")
def department_edit(qid):
    return _list_edit("department", qid)


@staff_bp.route("/personnel/departements/<int:qid>/<any(archive,reactivate,delete):what>",
                methods=["POST"])
@login_required
@require_perm("staff.create")
def department_action(qid, what):
    return _list_action("department", qid, what)


# ── The merge screen ─────────────────────────────────────────────────────────


def _tokens(text):
    """Lower case, accents and punctuation off, initials ("M.") dropped."""
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = "".join(c if c.isalnum() else " " for c in s)
    return sorted(w for w in s.split() if len(w) > 1)


def looks_alike(a, b):
    """Two ways of writing what may be one person: the same words in another
    order or spelling, one name inside the other, or a near typo."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    sa, sb = set(ta), set(tb)
    small = sa if len(sa) < len(sb) else sb
    if (sa < sb or sb < sa) and any(len(w) >= 3 for w in small):
        return True
    return SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.85


def _pairs():
    """The doubtful cases still to decide: two files, or a file and a login."""
    people = Staff.query.order_by(Staff.last_name, Staff.first_name).all()
    dismissed = PersonMatchDismissed.query.all()
    gone_people = {(d.staff_a_id, d.staff_b_id) for d in dismissed if d.staff_b_id}
    gone_logins = {(d.staff_a_id, d.user_id) for d in dismissed if d.user_id}

    person_pairs = []
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            key = (min(a.id, b.id), max(a.id, b.id))
            if key in gone_people:
                continue
            if looks_alike(a.name, b.name):
                person_pairs.append((a, b))

    logins = User.query.filter(User.staff_id.is_(None),
                               User.is_super_admin.is_(False)).order_by(User.full_name).all()
    login_pairs = []
    for u in logins:
        for p in people:
            if p.login is None and p.is_employee and (p.id, u.id) not in gone_logins \
                    and looks_alike(p.name, u.full_name):
                login_pairs.append((p, u))
    return person_pairs, login_pairs


def merge_people(keep, drop):
    """Fold `drop` into `keep`: fleets, records, costs, papers and login.
    Returns an error string or None."""
    if keep.login is not None and drop.login is not None:
        return get_t()["merge.err.two_logins"]
    for col in _MERGE_FILL:
        if getattr(keep, col) in (None, "") and getattr(drop, col) not in (None, ""):
            setattr(keep, col, getattr(drop, col))
    if drop.kind == "employee":
        keep.kind = "employee"
    keep.is_active = keep.is_active or drop.is_active

    mine = {o.fleet_id: o for o in keep.drives}
    for link in list(drop.drives):
        twin = mine.get(link.fleet_id)
        if twin is None:
            link.staff_id = keep.id
        else:
            twin.is_active = twin.is_active or link.is_active
            (Vehicle.query.filter_by(default_operator_id=link.id)
             .update({Vehicle.default_operator_id: twin.id}, synchronize_session=False))
            db.session.delete(link)

    Expense.query.filter_by(staff_id=drop.id).update(
        {Expense.staff_id: keep.id}, synchronize_session=False)
    for model in DRIVER_RECORDS:
        model.query.filter_by(driver_id=drop.id).update(
            {model.driver_id: keep.id}, synchronize_session=False)
    StaffDocument.query.filter_by(staff_id=drop.id).update(
        {StaffDocument.staff_id: keep.id}, synchronize_session=False)
    PersonMatchDismissed.query.filter(db.or_(
        PersonMatchDismissed.staff_a_id == drop.id,
        PersonMatchDismissed.staff_b_id == drop.id)).delete(synchronize_session=False)
    login = drop.login
    if login is not None:
        login.staff_id = None
    if keep.photo_key == drop.photo_key:
        drop.photo_key = None
    db.session.flush()
    db.session.expire(drop)
    db.session.delete(drop)
    db.session.flush()
    if login is not None:
        login.staff_id = keep.id
    db.session.flush()
    db.session.refresh(keep)
    sync_login(keep)
    return None


@staff_bp.route("/personnel/doublons", methods=["GET", "POST"])
@login_required
def merge():
    if not current_user.is_super_admin:
        abort(403)
    t = get_t()
    if request.method == "POST":
        what = request.form.get("what")
        if what in ("link_name", "ignore_name"):
            name = request.form.get("name") or ""
            if not name:
                abort(400)
            if what == "link_name":
                person = db.session.get(Staff, request.form.get("person", type=int) or 0)
                if person is None:
                    flash("error|" + t["merge.err.pick_person"])
                    return redirect(url_for("staff.merge"))
                n = link_driver_name(name, person)
                log_action("UPDATE", "staff", resource_id=person.id,
                           detail="Linked %d record(s) named '%s' to '%s'" % (n, name, person.name))
                flash("success|" + t["merge.name_linked"] % {"n": n, "name": person.name})
            else:
                if not DriverNameIgnored.query.filter_by(name=name).first():
                    db.session.add(DriverNameIgnored(name=name[:120], created_by=current_user.id))
            db.session.commit()
            return redirect(url_for("staff.merge"))
        a = db.session.get(Staff, request.form.get("a", type=int) or 0)
        if a is None:
            abort(404)
        if what in ("merge", "different"):
            b = db.session.get(Staff, request.form.get("b", type=int) or 0)
            if b is None or b.id == a.id:
                abort(404)
            if what == "merge":
                keep_id = request.form.get("keep", type=int)
                keep, drop = (b, a) if keep_id == b.id else (a, b)
                kept, dropped = keep.name, drop.name
                err = merge_people(keep, drop)
                if err:
                    db.session.rollback()
                    flash("error|" + err)
                    return redirect(url_for("staff.merge"))
                log_action("MERGE", "staff", resource_id=keep.id,
                           detail="Merged '%s' into '%s'" % (dropped, kept))
                flash("success|" + t["merge.done"])
            else:
                db.session.add(PersonMatchDismissed(
                    staff_a_id=min(a.id, b.id), staff_b_id=max(a.id, b.id),
                    created_by=current_user.id))
        elif what in ("link", "unrelated"):
            u = db.session.get(User, request.form.get("u", type=int) or 0)
            if u is None:
                abort(404)
            if what == "link":
                if a.login is not None or u.staff_id is not None:
                    flash("error|" + t["merge.err.taken"])
                    return redirect(url_for("staff.merge"))
                if not a.phone and u.phone:
                    a.phone = u.phone
                u.staff_id = a.id
                db.session.flush()
                db.session.refresh(a)
                sync_login(a)
                log_action("UPDATE", "user", resource_id=u.id,
                           detail="Login '%s' tied to staff '%s'" % (u.username, a.name))
                flash("success|" + t["merge.linked"])
            else:
                db.session.add(PersonMatchDismissed(
                    staff_a_id=a.id, user_id=u.id, created_by=current_user.id))
        else:
            abort(400)
        db.session.commit()
        return redirect(url_for("staff.merge"))

    person_pairs, login_pairs = _pairs()
    # Names on records that link no file, each with the person it most
    # looks like, offered first.
    people = Staff.query.order_by(Staff.last_name, Staff.first_name).all()
    names = []
    for name, count in unlinked_driver_names():
        guess = next((p for p in people if looks_alike(p.name, name)), None)
        names.append({"name": name, "count": count, "guess": guess})
    return render_template("staff_merge.html", person_pairs=person_pairs,
                           login_pairs=login_pairs, names=names, people=people)
