"""Admin blueprint — the configuration pages.

Two tiers of access live here:

  * Fleets, roles and vehicle categories are *delegatable*: they are guarded by
    the `admin.fleets` / `admin.roles` / `admin.categories` permissions, so the
    super admin can hand them to a role (a Manager, typically) instead of doing
    every setup change themselves. See ASSIGNABLE_ADMIN_PERMS below.
  * Users, settings and the audit log stay super-admin-only — they are the keys
    to the house, not day-to-day setup.

Helpers (require_perm, super_admin_required, log_action, slugify, get_t) are
imported from app.py; this module is imported at the bottom of app.py once
those exist, so there is no circular-import problem.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required

from app import (HIDDEN_PERMS, get_t, has_perm, is_modal_request, log_action,
                 modal_ok, require_perm, slugify, super_admin_required,
                 tombstone_system_role)
from billing import current_rates
from models import (AppSetting, AuditLog, Fleet, FleetRate, Operator,
                    Permission, Role, RolePermission, User, UserFleet, Vehicle,
                    VehicleCategory, db)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# The Administration permissions the super admin may hand to a role. Everything
# else in that category (admin.users, admin.settings) stays super-admin-only and
# is never rendered in the role grid.
ASSIGNABLE_ADMIN_PERMS = ("admin.fleets", "admin.roles", "admin.categories")

OWN_ROLE_MSG = ("Vous ne pouvez pas modifier votre propre rôle. "
                "Demandez au super administrateur.")


def _valid_iso_date(s):
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


# ── Fleets ────────────────────────────────────────────────────────────────────


@admin_bp.route("/fleets")
@login_required
@require_perm("admin.fleets")
def fleets():
    show_archived = request.args.get("archived") == "1"
    rows = (Fleet.query.filter(Fleet.is_active.is_(not show_archived))
            .order_by(Fleet.name).all())
    counts = {
        f.id: {
            "vehicles": Vehicle.query.filter_by(fleet_id=f.id).count(),
            "users": UserFleet.query.filter_by(fleet_id=f.id).count(),
        }
        for f in rows
    }
    cat_labels = {c.code: c for c in VehicleCategory.query.all()}
    archived_count = Fleet.query.filter(Fleet.is_active.is_(False)).count()
    return render_template(
        "admin_fleets.html", fleets=rows, counts=counts, cat_labels=cat_labels,
        show_archived=show_archived, archived_count=archived_count,
    )


def _render_fleet_form(fleet, error=None):
    """Render the fleet form as a modal partial or a full page."""
    categories = VehicleCategory.query.order_by(VehicleCategory.sort_order).all()
    if request.method == "POST":
        selected = request.form.getlist("categories")
    else:
        selected = list(fleet.categories or []) if fleet else []
    # Current billing rate per category (in force today) to prefill the inputs.
    rates = current_rates(fleet.id) if fleet else {}
    tpl = "_fleet_form.html" if is_modal_request() else "admin_fleet_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, fleet=fleet, categories=categories,
                           selected_codes=selected, rates=rates,
                           today=date.today().isoformat(), error=error), status


@admin_bp.route("/fleets/new", methods=["GET", "POST"])
@login_required
@require_perm("admin.fleets")
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
@require_perm("admin.fleets")
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
@require_perm("admin.fleets")
def fleet_delete(fleet_id):
    """Soft delete: archive the fleet (preserves its vehicles/history)."""
    fleet = db.session.get(Fleet, fleet_id)
    if not fleet:
        abort(404)
    t = get_t()
    fleet.is_active = False
    log_action("ARCHIVE", "fleet", resource_id=fleet_id,
               detail=f"Archived fleet '{fleet.name}'")
    db.session.commit()
    flash("success|" + t.get("fleet.archived", "Flotte archivée."))
    return redirect(url_for("admin.fleets"))


@admin_bp.route("/fleets/<int:fleet_id>/reactivate", methods=["POST"])
@login_required
@require_perm("admin.fleets")
def fleet_reactivate(fleet_id):
    fleet = db.session.get(Fleet, fleet_id)
    if not fleet:
        abort(404)
    fleet.is_active = True
    log_action("REACTIVATE", "fleet", resource_id=fleet_id,
               detail=f"Reactivated fleet '{fleet.name}'")
    db.session.commit()
    flash("success|" + get_t().get("fleet.reactivated", "Flotte réactivée."))
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

    # Billing rates — append a dated FleetRate row only where the entered rate
    # differs from the one currently in force (history is never overwritten).
    eff = (request.form.get("rate_effective_from") or "").strip()
    if not _valid_iso_date(eff):
        eff = date.today().isoformat()
    in_force = current_rates(fleet.id)
    rate_changes = 0
    for code in categories:
        raw = (request.form.get("rate_" + code) or "").strip().replace(" ", "")
        if not raw:
            continue
        try:
            new_rate = int(round(float(raw.replace(",", "."))))
        except ValueError:
            return t.get("fleet.err.bad_rate", "Tarif invalide.")
        if new_rate < 0:
            return t.get("fleet.err.bad_rate", "Tarif invalide.")
        if in_force.get(code) != new_rate:
            db.session.add(FleetRate(
                fleet_id=fleet.id, category_code=code, rate_per_unit=new_rate,
                effective_from=eff, created_by=current_user.id,
            ))
            rate_changes += 1

    log_action(
        "CREATE" if creating else "UPDATE", "fleet",
        resource_id=fleet.id, fleet_id=fleet.id,
        detail=f"{'Created' if creating else 'Updated'} fleet "
               f"'{name}' ({len(categories)} categories, {rate_changes} rate changes)",
    )
    db.session.commit()
    return None


# ── Roles ─────────────────────────────────────────────────────────────────────

# Permission grid layout: resources (rows grouped) × actions.
RES_ORDER = ["dashboard", "vehicle", "operator", "entry", "maintenance_record",
             "maintenance_rule", "alert", "expense", "insights", "invoicing",
             "report", "admin"]
ACTION_ORDER = ["view", "create", "edit", "delete", "export", "resolve",
                "dismiss", "fleets", "roles", "categories"]


def _editable_perms():
    """The permissions the *current* user is allowed to grant to a role.

    Three filters, in order:
      1. modules hidden app-wide (app.HIDDEN_PERMS) are never grantable;
      2. of the Administration category only ASSIGNABLE_ADMIN_PERMS shows up —
         user management and settings are not delegatable;
      3. you can only delegate downwards: a non-super-admin sees exactly the
         permissions they hold themselves, so nobody can grant more power than
         they have. Grants outside this list are carried over untouched on save.
    """
    perms = [p for p in Permission.query.all()
             if p.key not in HIDDEN_PERMS
             and (p.category != "Administration" or p.key in ASSIGNABLE_ADMIN_PERMS)]
    if not current_user.is_super_admin:
        perms = [p for p in perms if has_perm(p.key)]
    return perms


def _can_delegate_approval():
    """Only someone who can approve may hand approval authority to a role."""
    return (current_user.is_super_admin
            or any(uf.role and uf.role.can_approve for uf in current_user.user_fleets))


def _owns_role(role):
    """True if the current user is assigned this role — you cannot edit the
    role you sit on (that is how you lock yourself out, or quietly promote
    yourself). The super admin is exempt: they hold no role."""
    if not role or current_user.is_super_admin:
        return False
    return any(uf.role_id == role.id for uf in current_user.user_fleets)


def _permission_groups():
    """[(resource, [permissions ordered by action])] over _editable_perms()."""
    perms = _editable_perms()
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
@require_perm("admin.roles")
def roles():
    show_archived = request.args.get("archived") == "1"
    rows = (Role.query.filter(Role.is_active.is_(not show_archived))
            .order_by(Role.is_system.desc(), Role.name).all())
    counts = {r.id: len(r.role_permissions) for r in rows}
    # How many people hold each role — decides whether it can be deleted at all.
    held = {r.id: UserFleet.query.filter_by(role_id=r.id).count() for r in rows}
    archived_count = Role.query.filter(Role.is_active.is_(False)).count()
    own_ids = {uf.role_id for uf in current_user.user_fleets} if not current_user.is_super_admin else set()
    return render_template("admin_roles.html", roles=rows, counts=counts, held=held,
                           show_archived=show_archived, archived_count=archived_count,
                           own_role_ids=own_ids)


@admin_bp.route("/roles/new", methods=["GET", "POST"])
@login_required
@require_perm("admin.roles")
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
                           groups=_permission_groups(), state=state,
                           can_delegate_approval=_can_delegate_approval())


@admin_bp.route("/roles/<int:role_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("admin.roles")
def role_edit(role_id):
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    if _owns_role(role):
        flash("error|" + get_t().get("role.err.own_role", OWN_ROLE_MSG))
        return redirect(url_for("admin.roles"))
    if request.method == "POST":
        error = _save_role(role)
        if error:
            flash("error|" + error)
        else:
            flash("success|" + get_t().get("role.updated", "Rôle mis à jour."))
            return redirect(url_for("admin.roles"))
    state = _submitted_state() if request.method == "POST" else _role_state_map(role)
    return render_template("admin_role_form.html", role=role,
                           groups=_permission_groups(), state=state,
                           can_delegate_approval=_can_delegate_approval())


@admin_bp.route("/roles/<int:role_id>/delete", methods=["POST"])
@login_required
@require_perm("admin.roles")
def role_delete(role_id):
    """Soft delete: archive the role (assignments are kept; reversible)."""
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    t = get_t()
    if _owns_role(role):
        flash("error|" + t.get("role.err.own_role", OWN_ROLE_MSG))
        return redirect(url_for("admin.roles"))
    role.is_active = False
    log_action("ARCHIVE", "role", resource_id=role_id,
               detail=f"Archived role '{role.name}'")
    db.session.commit()
    flash("success|" + t.get("role.archived", "Rôle archivé."))
    return redirect(url_for("admin.roles"))


@admin_bp.route("/roles/<int:role_id>/reactivate", methods=["POST"])
@login_required
@require_perm("admin.roles")
def role_reactivate(role_id):
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    role.is_active = True
    log_action("REACTIVATE", "role", resource_id=role_id,
               detail=f"Reactivated role '{role.name}'")
    db.session.commit()
    flash("success|" + get_t().get("role.reactivated", "Rôle réactivé."))
    return redirect(url_for("admin.roles"))


@admin_bp.route("/roles/<int:role_id>/destroy", methods=["POST"])
@login_required
@require_perm("admin.roles")
def role_destroy(role_id):
    """Delete a role for good, unlike archiving.

    Only safe while nobody holds it: user_fleets.role_id is NOT NULL, so
    deleting an assigned role would strand its users without access. The
    role's permission rows go with it (cascade on the relationship).
    """
    role = db.session.get(Role, role_id)
    if not role:
        abort(404)
    t = get_t()
    if _owns_role(role):
        flash("error|" + t.get("role.err.own_role", OWN_ROLE_MSG))
        return redirect(url_for("admin.roles"))
    held = UserFleet.query.filter_by(role_id=role.id).count()
    if held:
        flash("error|" + t.get("role.err.in_use", "Ce rôle est attribué à des utilisateurs.")
              + " (%d)" % held)
        return redirect(url_for("admin.roles"))
    name = role.name
    log_action("DELETE", "role", resource_id=role_id, detail=f"Deleted role '{name}'")
    # `flask seed` runs on every boot and would re-create a system role, so
    # record that this deletion was deliberate.
    if role.is_system:
        tombstone_system_role(role.slug)
    db.session.delete(role)
    db.session.commit()
    flash("success|" + t.get("role.deleted", "Rôle supprimé.") + " — " + name)
    return redirect(url_for("admin.roles"))


def _save_role(role):
    t = get_t()
    if _owns_role(role):
        return t.get("role.err.own_role", OWN_ROLE_MSG)
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

    if not _can_delegate_approval():
        can_approve = role.can_approve if role else False

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

    # Rebuild the role's permissions from the tri-state grid. Anything outside
    # the editor's reach (hidden modules, non-delegatable admin perms, powers
    # the editor doesn't hold) has no radio on the form, so carry it over
    # instead of silently dropping it.
    editable = _editable_perms()
    editable_ids = {p.id for p in editable}
    kept = [(rp.permission_id, rp.requires_approval)
            for rp in role.role_permissions
            if rp.permission_id not in editable_ids]
    RolePermission.query.filter_by(role_id=role.id).delete()
    for pid, approval in kept:
        db.session.add(RolePermission(role_id=role.id, permission_id=pid,
                                      requires_approval=approval))
    for p in editable:
        v = request.form.get("perm_%d" % p.id)
        # Admin permissions gate a config screen outright — there is no
        # "submit for approval" path behind them, so they are grant-or-not.
        if v == "direct" or (v == "approval" and p.category == "Administration"):
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
    show_archived = request.args.get("archived") == "1"
    rows = (User.query.filter_by(is_super_admin=False)
            .filter(User.is_active.is_(not show_archived))
            .order_by(User.username).all())
    fleets_by_id = {f.id: f for f in Fleet.query.all()}
    roles_by_id = {r.id: r for r in Role.query.all()}
    assigns = {
        u.id: [(fleets_by_id.get(uf.fleet_id), roles_by_id.get(uf.role_id))
               for uf in UserFleet.query.filter_by(user_id=u.id).all()]
        for u in rows
    }
    archived_count = User.query.filter_by(is_super_admin=False).filter(User.is_active.is_(False)).count()
    return render_template("admin_users.html", users=rows, assigns=assigns,
                           show_archived=show_archived, archived_count=archived_count)


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
    """Soft delete: archive (deactivate) the user — keeps their audit trail."""
    user = _get_managed_user(user_id)
    t = get_t()
    user.is_active = False
    log_action("ARCHIVE", "user", resource_id=user_id, detail="Archived user '%s'" % user.username)
    db.session.commit()
    flash("success|" + t.get("user.archived", "Utilisateur archivé."))
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
@login_required
@super_admin_required
def user_reactivate(user_id):
    user = _get_managed_user(user_id)
    user.is_active = True
    log_action("REACTIVATE", "user", resource_id=user_id, detail="Reactivated user '%s'" % user.username)
    db.session.commit()
    flash("success|" + get_t().get("user.reactivated", "Utilisateur réactivé."))
    return redirect(url_for("admin.users"))


# ── Vehicle categories ──────────────────────────────────────────────────────────

# How a category's daily entries are logged (chosen at creation).
# unit_type is derived: "trips" for "trips", "hours" for the hours_* modes.
CATEGORY_TRACKING = ["trips", "hours", "hours_index"]


def _cat_num(raw, cast):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None, False
    try:
        return cast(raw), False
    except (TypeError, ValueError):
        return None, True


@admin_bp.route("/categories")
@login_required
@require_perm("admin.categories")
def categories():
    show_archived = request.args.get("archived") == "1"
    rows = (VehicleCategory.query.filter(VehicleCategory.is_active.is_(not show_archived))
            .order_by(VehicleCategory.sort_order, VehicleCategory.code).all())
    counts = {c.id: Vehicle.query.filter_by(category_id=c.id).count() for c in rows}
    archived_count = VehicleCategory.query.filter(VehicleCategory.is_active.is_(False)).count()
    return render_template("admin_categories.html", categories=rows, counts=counts,
                           show_archived=show_archived, archived_count=archived_count)


def _save_category(cat):
    t = get_t()
    label = (request.form.get("label") or "").strip()
    label_fr = (request.form.get("label_fr") or "").strip()
    tracking = (request.form.get("tracking") or "").strip()
    if not label or not label_fr:
        return t.get("vcat.err.label_required", "Les libelles sont obligatoires.")
    if tracking not in CATEGORY_TRACKING:
        return t.get("vcat.err.unit_required", "Choisissez une methode de suivi.")
    unit_type = "trips" if tracking == "trips" else "hours"

    creating = cat is None
    if creating:
        code = (request.form.get("code") or "").strip().upper().replace(" ", "_")
        if not code:
            return t.get("vcat.err.code_required", "Le code est obligatoire.")
        if VehicleCategory.query.filter(db.func.upper(VehicleCategory.code) == code).first():
            return t.get("vcat.err.code_taken", "Ce code existe deja.")

    baseline, e1 = _cat_num(request.form.get("default_baseline_l_per_unit"), float)
    order, e3 = _cat_num(request.form.get("sort_order"), lambda s: int(round(float(s))))
    if e1 or e3:
        return t.get("vcat.err.bad_number", "Valeur numerique invalide.")

    if creating:
        cat = VehicleCategory(code=code)
        db.session.add(cat)
    cat.label = label
    cat.label_fr = label_fr
    cat.tracking = tracking
    cat.unit_type = unit_type
    cat.default_baseline_l_per_unit = baseline
    cat.sort_order = order if order is not None else 0
    db.session.flush()
    log_action("CREATE" if creating else "UPDATE", "vehicle_category", resource_id=cat.id,
               detail="%s category '%s'" % ("Created" if creating else "Updated", cat.code))
    db.session.commit()
    return None


def _render_category_form(cat, error=None):
    tpl = "_category_form.html" if is_modal_request() else "admin_category_form.html"
    status = 422 if (error and is_modal_request()) else 200
    return render_template(tpl, category=cat, tracking_modes=CATEGORY_TRACKING, error=error), status


@admin_bp.route("/categories/new", methods=["GET", "POST"])
@login_required
@require_perm("admin.categories")
def category_new():
    if request.method == "POST":
        error = _save_category(None)
        if error:
            return _render_category_form(None, error)
        flash("success|" + get_t().get("vcat.created", "Categorie creee."))
        return modal_ok() if is_modal_request() else redirect(url_for("admin.categories"))
    return _render_category_form(None)


@admin_bp.route("/categories/<int:cat_id>/edit", methods=["GET", "POST"])
@login_required
@require_perm("admin.categories")
def category_edit(cat_id):
    cat = db.session.get(VehicleCategory, cat_id)
    if not cat:
        abort(404)
    if request.method == "POST":
        error = _save_category(cat)
        if error:
            return _render_category_form(cat, error)
        flash("success|" + get_t().get("vcat.updated", "Categorie mise a jour."))
        return modal_ok() if is_modal_request() else redirect(url_for("admin.categories"))
    return _render_category_form(cat)


@admin_bp.route("/categories/<int:cat_id>/delete", methods=["POST"])
@login_required
@require_perm("admin.categories")
def category_delete(cat_id):
    """Soft delete: archive the category (vehicles/fleets keep referencing it)."""
    cat = db.session.get(VehicleCategory, cat_id)
    if not cat:
        abort(404)
    t = get_t()
    cat.is_active = False
    log_action("ARCHIVE", "vehicle_category", resource_id=cat_id, detail="Archived category '%s'" % cat.code)
    db.session.commit()
    flash("success|" + t.get("vcat.archived", "Catégorie archivée."))
    return redirect(url_for("admin.categories"))


@admin_bp.route("/categories/<int:cat_id>/reactivate", methods=["POST"])
@login_required
@require_perm("admin.categories")
def category_reactivate(cat_id):
    cat = db.session.get(VehicleCategory, cat_id)
    if not cat:
        abort(404)
    cat.is_active = True
    log_action("REACTIVATE", "vehicle_category", resource_id=cat_id, detail="Reactivated category '%s'" % cat.code)
    db.session.commit()
    flash("success|" + get_t().get("vcat.reactivated", "Catégorie réactivée."))
    return redirect(url_for("admin.categories"))


# ── Audit log ─────────────────────────────────────────────────────────────────

AUDIT_PER_PAGE = 50


@admin_bp.route("/audit")
@login_required
@super_admin_required
def audit():
    """Read-only viewer over the AuditLog trail, with filters + pagination."""
    flt = {
        "q":         (request.args.get("q") or "").strip(),
        "action":    (request.args.get("action") or "").strip(),
        "resource":  (request.args.get("resource") or "").strip(),
        "fleet_id":  request.args.get("fleet_id", type=int),
        "date_from": (request.args.get("date_from") or "").strip(),
        "date_to":   (request.args.get("date_to") or "").strip(),
    }
    page = request.args.get("page", 1, type=int)

    query = AuditLog.query
    if flt["q"]:
        query = query.filter(AuditLog.username.ilike("%" + flt["q"] + "%"))
    if flt["action"]:
        query = query.filter(AuditLog.action == flt["action"])
    if flt["resource"]:
        query = query.filter(AuditLog.resource_type == flt["resource"])
    if flt["fleet_id"]:
        query = query.filter(AuditLog.fleet_id == flt["fleet_id"])
    if flt["date_from"]:
        try:
            query = query.filter(AuditLog.timestamp >= datetime.strptime(flt["date_from"], "%Y-%m-%d"))
        except ValueError:
            pass
    if flt["date_to"]:
        try:
            query = query.filter(AuditLog.timestamp < datetime.strptime(flt["date_to"], "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=AUDIT_PER_PAGE, error_out=False
    )

    actions   = [a[0] for a in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    resources = [r[0] for r in db.session.query(AuditLog.resource_type).distinct().order_by(AuditLog.resource_type).all()]
    fleets    = Fleet.query.order_by(Fleet.name).all()

    return render_template(
        "admin_audit.html",
        pagination=pagination,
        logs=pagination.items,
        actions=actions,
        resources=resources,
        fleets=fleets,
        fleets_by_id={fl.id: fl for fl in fleets},
        filters=flt,
        has_filters=any(flt.values()),
    )


# ── Settings ──────────────────────────────────────────────────────────────────

# Drives input rendering + validation per setting key. Keys not listed here
# fall back to a free-text input with no validation.
SETTINGS_SCHEMA = {
    "grace_period_minutes": {"type": "number", "min": 0, "max": 1440},
    "currency":             {"type": "select", "options": ["GNF", "USD", "EUR", "XOF"]},
    "default_lang":         {"type": "select", "options": ["fr", "en"]},
}


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@super_admin_required
def settings():
    t = get_t()
    rows = (AppSetting.query.filter(AppSetting.category != "internal")
            .order_by(AppSetting.category, AppSetting.key).all())

    if request.method == "POST":
        errors = {}
        for s in rows:
            if s.key not in request.form:
                continue
            raw = (request.form.get(s.key) or "").strip()
            spec = SETTINGS_SCHEMA.get(s.key, {})
            kind = spec.get("type")

            if kind == "number":
                if not raw.lstrip("-").isdigit():
                    errors[s.key] = t.get("setting.err.number", "Valeur numérique invalide.")
                    continue
                n = int(raw)
                lo, hi = spec.get("min"), spec.get("max")
                if (lo is not None and n < lo) or (hi is not None and n > hi):
                    errors[s.key] = t.get("setting.err.range", "Valeur hors limites.")
                    continue
                raw = str(n)
            elif kind == "select":
                if raw not in spec["options"]:
                    errors[s.key] = t.get("setting.err.choice", "Choix invalide.")
                    continue

            s.value = raw
            s.updated_by = current_user.id

        if errors:
            db.session.rollback()
            return render_template(
                "admin_settings.html",
                groups=_settings_groups(rows), schema=SETTINGS_SCHEMA,
                errors=errors, form=request.form,
            )

        log_action("UPDATE", "app_setting", detail="Updated %d settings" % len(rows))
        db.session.commit()
        flash("success|" + t.get("setting.saved", "Paramètres enregistrés."))
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin_settings.html",
        groups=_settings_groups(rows), schema=SETTINGS_SCHEMA,
        errors={}, form=None,
    )


def _settings_groups(rows):
    """Group settings by category, preserving query order within each."""
    grouped = {}
    for s in rows:
        grouped.setdefault(s.category, []).append(s)
    return grouped
