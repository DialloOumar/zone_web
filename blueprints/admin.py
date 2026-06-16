"""Admin blueprint — super-admin-only configuration pages.

Currently implements Fleets. Users, roles, vehicle categories, settings, and
the audit log follow as further route groups in this same blueprint.

Helpers (super_admin_required, log_action, slugify, get_t) are imported from
app.py; this module is imported at the bottom of app.py once those exist, so
there is no circular-import problem.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (get_t, is_modal_request, log_action, modal_ok, slugify,
                 super_admin_required)
from models import (Fleet, Operator, Permission, Role, RolePermission, User,
                    UserFleet, Vehicle, VehicleCategory, db)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Fleets ────────────────────────────────────────────────────────────────────


@admin_bp.route("/fleets")
@login_required
@super_admin_required
def fleets():
    rows = Fleet.query.order_by(Fleet.name).all()
    counts = {
        f.id: {
            "vehicles": Vehicle.query.filter_by(fleet_id=f.id).count(),
            "users": UserFleet.query.filter_by(fleet_id=f.id).count(),
        }
        for f in rows
    }
    cat_labels = {c.code: c for c in VehicleCategory.query.all()}
    return render_template(
        "admin_fleets.html", fleets=rows, counts=counts, cat_labels=cat_labels
    )


def _render_fleet_form(fleet, error=None):
    """Render the fleet form as a modal partial or a full page."""
    categories = VehicleCategory.query.order_by(VehicleCategory.sort_order).all()
    if request.method == "POST":
        selected = request.form.getlist("categories")
    else:
        selected = list(fleet.categories or []) if fleet else []
    tpl = "_fleet_form.html" if is_modal_request() else "admin_fleet_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, fleet=fleet, categories=categories,
                           selected_codes=selected, error=error), status


@admin_bp.route("/fleets/new", methods=["GET", "POST"])
@login_required
@super_admin_required
def fleet_new():
    if request.method == "POST":
        error = _save_fleet(None)
        if error:
            return _render_fleet_form(None, error)
        flash("success|" + get_t().get("fleet.created", "Flotte créée."))
        return modal_ok() if is_modal_request() else redirect(url_for("admin.fleets"))
    return _render_fleet_form(None)


@admin_bp.route("/fleets/<int:fleet_id>/edit", methods=["GET", "POST"])
@login_required
@super_admin_required
def fleet_edit(fleet_id):
    fleet = db.session.get(Fleet, fleet_id)
    if not fleet:
        abort(404)
    if request.method == "POST":
        error = _save_fleet(fleet)
        if error:
            return _render_fleet_form(fleet, error)
        flash("success|" + get_t().get("fleet.updated", "Flotte mise à jour."))
        return modal_ok() if is_modal_request() else redirect(url_for("admin.fleets"))
    return _render_fleet_form(fleet)


@admin_bp.route("/fleets/<int:fleet_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def fleet_delete(fleet_id):
    fleet = db.session.get(Fleet, fleet_id)
    if not fleet:
        abort(404)
    t = get_t()
    # Refuse to delete while anything still references the fleet — deleting it
    # would orphan vehicles, operators, or user access grants.
    blocked = (
        Vehicle.query.filter_by(fleet_id=fleet_id).count()
        or Operator.query.filter_by(fleet_id=fleet_id).count()
        or UserFleet.query.filter_by(fleet_id=fleet_id).count()
    )
    if blocked:
        flash("error|" + t.get(
            "fleet.err.delete_blocked",
            "Impossible de supprimer : des véhicules, conducteurs ou "
            "utilisateurs sont encore rattachés à cette flotte.",
        ))
        return redirect(url_for("admin.fleets"))
    name = fleet.name
    db.session.delete(fleet)
    log_action("DELETE", "fleet", resource_id=fleet_id,
               detail=f"Deleted fleet '{name}'")
    db.session.commit()
    flash("success|" + t.get("fleet.deleted", "Flotte supprimée."))
    return redirect(url_for("admin.fleets"))


def _save_fleet(fleet):
    """Create (fleet=None) or update a fleet from request.form.

    Returns a localized error message on validation failure, or None on
    success (in which case the change is committed and audit-logged).
    """
    t = get_t()
    name = (request.form.get("name") or "").strip()
    slug_in = (request.form.get("slug") or "").strip()
    description = (request.form.get("description") or "").strip()
    selected = request.form.getlist("categories")

    if not name:
        return t.get("fleet.err.name_required", "Le nom est obligatoire.")

    slug = slugify(slug_in or name)

    # Uniqueness — case-insensitive on name, exact on slug, excluding self.
    name_clash = Fleet.query.filter(db.func.lower(Fleet.name) == name.lower())
    slug_clash = Fleet.query.filter(Fleet.slug == slug)
    if fleet:
        name_clash = name_clash.filter(Fleet.id != fleet.id)
        slug_clash = slug_clash.filter(Fleet.id != fleet.id)
    if name_clash.first():
        return t.get("fleet.err.name_taken", "Une flotte porte déjà ce nom.")
    if slug_clash.first():
        return t.get("fleet.err.slug_taken",
                     "Cet identifiant (slug) est déjà utilisé.")

    # Keep only codes that map to a real category.
    valid_codes = {c.code for c in VehicleCategory.query.all()}
    categories = [c for c in selected if c in valid_codes]

    creating = fleet is None
    if creating:
        fleet = Fleet(name=name, slug=slug,
                      description=description or None, categories=categories)
        db.session.add(fleet)
    else:
        fleet.name = name
        fleet.slug = slug
        fleet.description = description or None
        fleet.categories = categories
    db.session.flush()  # assign id for the audit log on create
    log_action(
        "CREATE" if creating else "UPDATE", "fleet",
        resource_id=fleet.id, fleet_id=fleet.id,
        detail=f"{'Created' if creating else 'Updated'} fleet "
               f"'{name}' ({len(categories)} categories)",
    )
    db.session.commit()
    return None


# ── Roles ─────────────────────────────────────────────────────────────────────

# Permission grid layout: resources (rows grouped) × actions.
RES_ORDER = ["vehicle", "operator", "entry", "maintenance_record",
             "maintenance_rule", "alert", "expense", "report"]
ACTION_ORDER = ["view", "create", "edit", "delete", "export", "resolve", "dismiss"]


def _permission_groups():
    """[(resource, [permissions ordered by action])] excluding admin perms."""
    perms = Permission.query.filter(Permission.category != "Administration").all()
    by_res = {}
    for p in perms:
        by_res.setdefault(p.resource, []).append(p)
    groups = []
    for res in RES_ORDER:
        if res in by_res:
            rows = sorted(by_res[res], key=lambda p: ACTION_ORDER.index(p.action)
                          if p.action in ACTION_ORDER else 99)
            groups.append((res, rows))
    return groups


def _role_state_map(role):
    """permission_id -> 'direct' | 'approval' for a role's current grants."""
    if not role:
        return {}
    return {rp.permission_id: ("approval" if rp.requires_approval else "direct")
            for rp in role.role_permissions}


def _submitted_state():
    state = {}
    for p in Permission.query.all():
        v = request.form.get("perm_%d" % p.id)
        if v in ("direct", "approval"):
            state[p.id] = v
    return state


@admin_bp.route("/roles")
@login_required
@super_admin_required
def roles():
    rows = Role.query.order_by(Role.is_system.desc(), Role.name).all()
    counts = {r.id: len(r.role_permissions) for r in rows}
    return render_template("admin_roles.html", roles=rows, counts=counts)


@admin_bp.route("/roles/new", methods=["GET", "POST"])
@login_required
@super_admin_required
def role_new():
    if request.method == "POST":
        error = _save_role(None)
        if error:
            flash("error|" + error)
        else:
            flash("success|" + get_t().get("role.created", "Rôle créé."))
            return redirect(url_for("admin.roles"))
    state = _submitted_state() if request.method == "POST" else {}
    return render_template("admin_role_form.html", role=None,
                           groups=_permission_groups(), state=state)


@admin_bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@login_required
@super_admin_required
def role_edit(role_id):
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    if request.method == "POST":
        error = _save_role(role)
        if error:
            flash("error|" + error)
        else:
            flash("success|" + get_t().get("role.updated", "Rôle mis à jour."))
            return redirect(url_for("admin.roles"))
    state = _submitted_state() if request.method == "POST" else _role_state_map(role)
    return render_template("admin_role_form.html", role=role,
                           groups=_permission_groups(), state=state)


@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def role_delete(role_id):
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    t = get_t()
    if role.is_system:
        flash("error|" + t.get("role.err.system", "Les rôles système ne peuvent pas être supprimés."))
        return redirect(url_for("admin.roles"))
    if UserFleet.query.filter_by(role_id=role_id).count():
        flash("error|" + t.get("role.err.in_use", "Ce rôle est attribué à des utilisateurs."))
        return redirect(url_for("admin.roles"))
    name = role.name
    db.session.delete(role)
    log_action("DELETE", "role", resource_id=role_id, detail="Deleted role '%s'" % name)
    db.session.commit()
    flash("success|" + t.get("role.deleted", "Rôle supprimé."))
    return redirect(url_for("admin.roles"))


def _save_role(role):
    t = get_t()
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    can_approve = request.form.get("can_approve") is not None
    if not name:
        return t.get("role.err.name_required", "Le nom est obligatoire.")
    clash = Role.query.filter(db.func.lower(Role.name) == name.lower())
    if role:
        clash = clash.filter(Role.id != role.id)
    if clash.first():
        return t.get("role.err.name_taken", "Un rôle porte déjà ce nom.")

    creating = role is None
    if creating:
        role = Role(name=name, slug=slugify(name), description=description or None,
                    can_approve=can_approve, is_system=False, created_by=current_user.id)
        db.session.add(role)
        db.session.flush()
    else:
        role.name = name
        role.description = description or None
        role.can_approve = can_approve

    # Rebuild the role's permissions from the tri-state grid.
    RolePermission.query.filter_by(role_id=role.id).delete()
    for p in Permission.query.filter(Permission.category != "Administration").all():
        v = request.form.get("perm_%d" % p.id)
        if v == "direct":
            db.session.add(RolePermission(role_id=role.id, permission_id=p.id, requires_approval=False))
        elif v == "approval":
            db.session.add(RolePermission(role_id=role.id, permission_id=p.id, requires_approval=True))
    log_action("CREATE" if creating else "UPDATE", "role", resource_id=role.id,
               detail="%s role '%s'" % ("Created" if creating else "Updated", name))
    db.session.commit()
    return None


# ── Users ──────────────────────────────────────────────────────────────────────


@admin_bp.route("/users")
@login_required
@super_admin_required
def users():
    rows = User.query.filter_by(is_super_admin=False).order_by(User.username).all()
    fleets_by_id = {f.id: f for f in Fleet.query.all()}
    roles_by_id = {r.id: r for r in Role.query.all()}
    assigns = {
        u.id: [(fleets_by_id.get(uf.fleet_id), roles_by_id.get(uf.role_id))
               for uf in UserFleet.query.filter_by(user_id=u.id).all()]
        for u in rows
    }
    return render_template("admin_users.html", users=rows, assigns=assigns)


def _create_user():
    t = get_t()
    username = (request.form.get("username") or "").strip().lower()
    full_name = (request.form.get("full_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower() or None
    password = request.form.get("password") or ""
    is_active = request.form.get("is_active") is not None
    if not username:
        return t.get("user.err.username_required", "Nom d'utilisateur obligatoire."), None
    if not full_name:
        return t.get("user.err.name_required", "Nom complet obligatoire."), None
    if len(password) < 6:
        return t.get("user.err.password_short", "Mot de passe : 6 caracteres minimum."), None
    if User.query.filter(db.func.lower(User.username) == username).first():
        return t.get("user.err.username_taken", "Ce nom d'utilisateur existe deja."), None
    if email and User.query.filter(db.func.lower(User.email) == email).first():
        return t.get("user.err.email_taken", "Ce courriel est deja utilise."), None
    u = User(username=username, full_name=full_name, email=email,
             is_active=is_active, lang="fr")
    u.set_password(password)
    db.session.add(u)
    db.session.flush()
    log_action("CREATE", "user", resource_id=u.id, detail="Created user '%s'" % username)
    db.session.commit()
    return None, u


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@super_admin_required
def user_new():
    if request.method == "POST":
        error, user = _create_user()
        if error:
            tpl = "_user_form.html" if is_modal_request() else "admin_user_new.html"
            status = 422 if (error and is_modal_request()) else 200
            return render_template(tpl, error=error), status
        flash("success|" + get_t().get("user.created", "Utilisateur cree."))
        if is_modal_request():
            return modal_ok()
        return redirect(url_for("admin.user_edit", user_id=user.id))
    tpl = "_user_form.html" if is_modal_request() else "admin_user_new.html"
    return render_template(tpl, error=None)


def _get_managed_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.is_super_admin:
        abort(404)
    return user


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@super_admin_required
def user_edit(user_id):
    user = _get_managed_user(user_id)
    t = get_t()
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower() or None
        if not username or not full_name:
            flash("error|" + t.get("user.err.name_required", "Champs obligatoires manquants."))
        elif User.query.filter(db.func.lower(User.username) == username, User.id != user.id).first():
            flash("error|" + t.get("user.err.username_taken", "Ce nom d'utilisateur existe deja."))
        elif email and User.query.filter(db.func.lower(User.email) == email, User.id != user.id).first():
            flash("error|" + t.get("user.err.email_taken", "Ce courriel est deja utilise."))
        else:
            user.username = username
            user.full_name = full_name
            user.email = email
            user.is_active = request.form.get("is_active") is not None
            log_action("UPDATE", "user", resource_id=user.id, detail="Updated user '%s'" % username)
            db.session.commit()
            flash("success|" + t.get("user.updated", "Utilisateur mis a jour."))
            return redirect(url_for("admin.users"))
    fleets = Fleet.query.order_by(Fleet.name).all()
    roles = Role.query.order_by(Role.is_system.desc(), Role.name).all()
    assignments = [
        (db.session.get(Fleet, uf.fleet_id), db.session.get(Role, uf.role_id), uf)
        for uf in UserFleet.query.filter_by(user_id=user.id).order_by(UserFleet.fleet_id).all()
    ]
    return render_template("admin_user_edit.html", user=user, fleets=fleets,
                           roles=roles, assignments=assignments)


@admin_bp.route("/users/<int:user_id>/password", methods=["POST"])
@login_required
@super_admin_required
def user_password(user_id):
    user = _get_managed_user(user_id)
    t = get_t()
    pw = request.form.get("password") or ""
    if len(pw) < 6:
        flash("error|" + t.get("user.err.password_short", "Mot de passe : 6 caracteres minimum."))
    else:
        user.set_password(pw)
        log_action("UPDATE", "user", resource_id=user.id, detail="Reset password")
        db.session.commit()
        flash("success|" + t.get("user.password_reset", "Mot de passe reinitialise."))
    return redirect(url_for("admin.user_edit", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/assign", methods=["POST"])
@login_required
@super_admin_required
def user_assign(user_id):
    user = _get_managed_user(user_id)
    t = get_t()
    fleet_id = request.form.get("fleet_id", type=int)
    role_id = request.form.get("role_id", type=int)
    fleet = db.session.get(Fleet, fleet_id) if fleet_id else None
    role = db.session.get(Role, role_id) if role_id else None
    if not fleet or not role:
        flash("error|" + t.get("user.err.assign", "Choisissez une flotte et un role."))
        return redirect(url_for("admin.user_edit", user_id=user.id))
    uf = UserFleet.query.filter_by(user_id=user.id, fleet_id=fleet.id).first()
    if uf:
        uf.role_id = role.id
    else:
        db.session.add(UserFleet(user_id=user.id, fleet_id=fleet.id,
                                 role_id=role.id, assigned_by=current_user.id))
    log_action("UPDATE", "user", resource_id=user.id, fleet_id=fleet.id,
               detail="Assigned %s as %s" % (fleet.name, role.name))
    db.session.commit()
    flash("success|" + t.get("user.assigned", "Acces attribue."))
    return redirect(url_for("admin.user_edit", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/unassign", methods=["POST"])
@login_required
@super_admin_required
def user_unassign(user_id):
    user = _get_managed_user(user_id)
    fleet_id = request.form.get("fleet_id", type=int)
    UserFleet.query.filter_by(user_id=user.id, fleet_id=fleet_id).delete()
    log_action("UPDATE", "user", resource_id=user.id, fleet_id=fleet_id, detail="Removed fleet access")
    db.session.commit()
    flash("success|" + get_t().get("user.unassigned", "Acces retire."))
    return redirect(url_for("admin.user_edit", user_id=user.id))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@super_admin_required
def user_delete(user_id):
    user = _get_managed_user(user_id)
    t = get_t()
    username = user.username
    db.session.delete(user)
    log_action("DELETE", "user", resource_id=user_id, detail="Deleted user '%s'" % username)
    db.session.commit()
    flash("success|" + t.get("user.deleted", "Utilisateur supprime."))
    return redirect(url_for("admin.users"))
