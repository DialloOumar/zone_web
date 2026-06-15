"""Zone Web — Flask application entry point.

Skeleton: auth (Flask-Login), permission helpers (has_perm), scope helper
(scoped) that filters by current user's category access, base routes, and
the bootstrap CLI commands (seed-permissions, seed-system-roles,
seed-default-categories, seed-super-admin).

Domain routes (vehicles, daily entries, maintenance, expenses, alerts,
admin pages) come in subsequent sprints.
"""
import logging
import os
from datetime import datetime, timedelta
from functools import wraps

import click
from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from flask_migrate import Migrate

from languages import TRANSLATIONS
from models import (Alert, AppSetting, AuditLog, DailyEntry, Expense, Fleet,
                    MaintenanceRecord, MaintenanceRule, PendingChange,
                    Permission, Role, RolePermission, User, UserFleet, Vehicle,
                    VehicleCategory, db)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("zone_web")


# ── App factory (single instance for simplicity, like batmex) ────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-fallback-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///zone.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload cap (Pillow-resized afterwards)

db.init_app(app)
migrate = Migrate(app, db)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def _load_user(user_id):
    return db.session.get(User, int(user_id))


# ── i18n helper ──────────────────────────────────────────────────────────────


def get_t():
    """Return the translation dict for the current user (default = fr)."""
    lang = (current_user.lang if current_user.is_authenticated else None) \
           or os.environ.get("APP_DEFAULT_LANG", "fr")
    return TRANSLATIONS.get(lang, TRANSLATIONS["fr"])


@app.context_processor
def _inject_globals():
    """Make `t` and `has_perm` directly callable inside Jinja."""
    return {
        "t": get_t(),
        "has_perm": has_perm,
        "is_super_admin": current_user.is_authenticated and current_user.is_super_admin,
        "app_settings": {s.key: s.value for s in AppSetting.query.all()} if current_user.is_authenticated else {},
    }


# ── Access control: categories, permissions, scoping ─────────────────────────


def current_user_categories():
    """Return the list of VehicleCategory codes the current user is allowed to
    act on, or None for super admin (no restriction).
    """
    if not current_user.is_authenticated:
        return []
    if current_user.is_super_admin:
        return None
    cats = set()
    for uf in current_user.user_fleets:
        if uf.fleet and uf.fleet.categories:
            cats.update(uf.fleet.categories)
    return sorted(cats)


def current_user_fleet_ids():
    if not current_user.is_authenticated:
        return []
    if current_user.is_super_admin:
        return None
    return [uf.fleet_id for uf in current_user.user_fleets]


def has_perm(perm_key):
    """True if the current user holds the given permission via any of their
    fleet assignments. Super admin always passes. Cached per request.
    """
    if not current_user.is_authenticated:
        return False
    if current_user.is_super_admin:
        return True
    cache = getattr(g, "_user_perms", None)
    if cache is None:
        perms = set()
        for uf in current_user.user_fleets:
            if uf.role:
                for rp in uf.role.role_permissions:
                    perms.add(rp.permission.key)
        g._user_perms = perms
        cache = perms
    return perm_key in cache


def can_approve_in_fleet(fleet_id):
    """True if the user's role in this fleet has can_approve=True."""
    if not current_user.is_authenticated:
        return False
    if current_user.is_super_admin:
        return True
    uf = next((uf for uf in current_user.user_fleets if uf.fleet_id == fleet_id), None)
    return bool(uf and uf.role and uf.role.can_approve)


def needs_approval(action_key, record_created_by=None, record_created_at=None):
    """Decide whether an action requires approval.

    Three short-circuits return False (action applies immediately):
      1. Super admin always direct.
      2. Fleet manager (can_approve) acting in their own fleet always direct.
      3. Original creator within the grace window always direct.

    Otherwise, look up the RolePermission for this action — if requires_approval
    is True, the action queues into PendingChange.
    """
    if not current_user.is_authenticated:
        return False
    if current_user.is_super_admin:
        return False
    if record_created_by == current_user.id and record_created_at:
        grace_minutes = int(_get_setting("grace_period_minutes", "15"))
        if datetime.utcnow() - record_created_at < timedelta(minutes=grace_minutes):
            return False
    # Look up the requires_approval flag on the user's effective role for this
    # permission. Across multiple fleets we conservatively pick "requires
    # approval" if ANY assignment marks it so — the user can submit via the
    # stricter fleet's queue.
    for uf in current_user.user_fleets:
        if uf.role:
            for rp in uf.role.role_permissions:
                if rp.permission.key == action_key:
                    return bool(rp.requires_approval)
    return False  # no permission at all — caller should have already 403'd


def require_perm(perm_key):
    """Decorator: 403 if the user doesn't have the permission."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not has_perm(perm_key):
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def super_admin_required(f):
    """Decorator: 403 unless the user is the super admin. Used on all
    configuration routes.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not (current_user.is_authenticated and current_user.is_super_admin):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def scoped(model, *, category_attr="category_id", fleet_attr="fleet_id"):
    """Return a base query filtered to what the current user is allowed to see.

    For models with a fleet_id column: filter by user's assigned fleets.
    For models like DailyEntry that go through Vehicle: caller passes their
    own join (this helper covers the simple direct-FK case only).
    """
    q = model.query
    if current_user.is_super_admin:
        return q
    fleet_ids = current_user_fleet_ids() or []
    if hasattr(model, fleet_attr):
        q = q.filter(getattr(model, fleet_attr).in_(fleet_ids))
    return q


# ── Audit helper ─────────────────────────────────────────────────────────────


def log_action(action, resource_type, *, resource_id=None, detail=None, fleet_id=None):
    """Record an audit log row. Best-effort — never raises."""
    try:
        if not current_user.is_authenticated:
            return
        # Snapshot the user's name + role at time of action — survives renames
        role_snapshot = "super_admin" if current_user.is_super_admin else (
            ", ".join(uf.role.name for uf in current_user.user_fleets if uf.role) or "—"
        )
        entry = AuditLog(
            user_id=current_user.id,
            username=current_user.username,
            actor_role=role_snapshot,
            fleet_id=fleet_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=(detail or "")[:255],
            ip_address=request.remote_addr,
        )
        db.session.add(entry)
    except Exception as e:
        log.warning("audit log failed: %s", e)


def _get_setting(key, default=None):
    s = db.session.get(AppSetting, key)
    return s.value if s else default


# ── Base routes (auth + landing placeholder) ─────────────────────────────────


@app.route("/")
@login_required
def dashboard():
    """Placeholder landing page. Real dashboard comes in sprint 2."""
    return render_template("dashboard.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            log_action("LOGIN", "auth", detail="Successful login")
            db.session.commit()
            return redirect(request.args.get("next") or url_for("dashboard"))
        # Log the attempt without exposing the user's typed username — the
        # request log + IP suffice for forensics. Avoid leaking that an
        # unknown username was attempted, to prevent username enumeration.
        log_action("LOGIN_FAILED", "auth", detail="Invalid credentials submitted")
        db.session.commit()
        error = get_t().get("auth.invalid_credentials", "Invalid credentials.")
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    log_action("LOGOUT", "auth")
    db.session.commit()
    logout_user()
    return redirect(url_for("login"))


@app.route("/healthz")
def healthz():
    """Liveness probe for Docker / load balancer."""
    return jsonify(status="ok"), 200


@app.errorhandler(403)
def _forbidden(_):
    return render_template("error.html", code=403, message=get_t().get("error.forbidden", "Forbidden")), 403


@app.errorhandler(404)
def _not_found(_):
    return render_template("error.html", code=404, message=get_t().get("error.not_found", "Not found")), 404


# ── CLI bootstrap commands ───────────────────────────────────────────────────


# Master permission list — seeded into the `permissions` table.
# Format: (key, label_en, category, resource, action)
PERMISSIONS_CATALOG = [
    # Vehicles
    ("vehicle.view",   "View vehicles",   "Vehicles", "vehicle", "view"),
    ("vehicle.create", "Create vehicle",  "Vehicles", "vehicle", "create"),
    ("vehicle.edit",   "Edit vehicle",    "Vehicles", "vehicle", "edit"),
    ("vehicle.delete", "Delete vehicle",  "Vehicles", "vehicle", "delete"),
    # Daily entries
    ("entry.view",   "View daily entries",   "Entries", "entry", "view"),
    ("entry.create", "Log daily entry",      "Entries", "entry", "create"),
    ("entry.edit",   "Edit daily entry",     "Entries", "entry", "edit"),
    ("entry.delete", "Delete daily entry",   "Entries", "entry", "delete"),
    # Maintenance
    ("maintenance_record.view",   "View maintenance records",   "Maintenance", "maintenance_record", "view"),
    ("maintenance_record.create", "Log maintenance",            "Maintenance", "maintenance_record", "create"),
    ("maintenance_record.edit",   "Edit maintenance record",    "Maintenance", "maintenance_record", "edit"),
    ("maintenance_record.delete", "Delete maintenance record",  "Maintenance", "maintenance_record", "delete"),
    ("maintenance_rule.view",   "View maintenance rules",   "Maintenance", "maintenance_rule", "view"),
    ("maintenance_rule.create", "Create maintenance rule",  "Maintenance", "maintenance_rule", "create"),
    ("maintenance_rule.edit",   "Edit maintenance rule",    "Maintenance", "maintenance_rule", "edit"),
    ("maintenance_rule.delete", "Delete maintenance rule",  "Maintenance", "maintenance_rule", "delete"),
    # Alerts
    ("alert.view",    "View alerts",   "Alerts", "alert", "view"),
    ("alert.resolve", "Resolve alert", "Alerts", "alert", "resolve"),
    ("alert.dismiss", "Dismiss alert", "Alerts", "alert", "dismiss"),
    # Expenses
    ("expense.view",   "View expenses",   "Expenses", "expense", "view"),
    ("expense.create", "Log expense",     "Expenses", "expense", "create"),
    ("expense.edit",   "Edit expense",    "Expenses", "expense", "edit"),
    ("expense.delete", "Delete expense",  "Expenses", "expense", "delete"),
    # Reports
    ("report.view",       "View reports",            "Reports", "report", "view"),
    ("report.export_pdf", "Export reports to PDF",   "Reports", "report", "export"),
    # Admin (super admin only — these aren't exposed in the role grid, just here for documentation)
    ("admin.users",      "Manage users",      "Administration", "admin", "users"),
    ("admin.fleets",     "Manage fleets",     "Administration", "admin", "fleets"),
    ("admin.roles",      "Manage roles",      "Administration", "admin", "roles"),
    ("admin.categories", "Manage categories", "Administration", "admin", "categories"),
    ("admin.settings",   "Manage settings",   "Administration", "admin", "settings"),
]


# Default vehicle categories — seeded into vehicle_categories.
# Format: (code, label_en, label_fr, unit_type, default_l_per_unit, default_cost_per_unit, sort_order)
DEFAULT_CATEGORIES = [
    ("BUS",         "Bus",             "Bus",                "trips",  12.0,  None,  1),
    ("MINIBUS",     "Minibus",         "Minibus",            "trips",   8.0,  None,  2),
    ("NAVETTE",     "Shuttle",         "Navette",            "trips",   6.0,  None,  3),
    ("CAMION_TSF",  "TSF Truck",       "Camion TSF",         "hours",  25.0,  None,  4),
    ("MACHINE_TSF", "TSF Machine",     "Machine TSF",        "hours",  30.0,  None,  5),
    ("CITERNE",     "Water tanker",    "Citerne à eau",      "trips",  15.0,  None,  6),
    ("SERVICE",     "Service vehicle", "Véhicule de service", "trips",  4.0,  None,  7),
    ("AUTRE",       "Other",           "Autre",              "trips",  None,  None,  8),
]


# Default app settings.
DEFAULT_SETTINGS = [
    ("grace_period_minutes", "15", "Grace period for self-corrections (minutes)", "approval"),
    ("currency",             "GNF", "Default currency",                            "general"),
    ("default_lang",         "fr",  "Default language",                            "general"),
]


# ── Shared seed helpers (called by both `flask seed` and the per-step CLI cmds) ─

# Map role slug → (name, can_approve, list of permission keys)
SYSTEM_ROLES = {
    "fleet_manager": ("Fleet Manager",  True, [
        "vehicle.view", "vehicle.create", "vehicle.edit", "vehicle.delete",
        "entry.view", "entry.create", "entry.edit", "entry.delete",
        "maintenance_record.view", "maintenance_record.create", "maintenance_record.edit", "maintenance_record.delete",
        "maintenance_rule.view", "maintenance_rule.create", "maintenance_rule.edit", "maintenance_rule.delete",
        "alert.view", "alert.resolve", "alert.dismiss",
        "expense.view", "expense.create", "expense.edit", "expense.delete",
        "report.view", "report.export_pdf",
    ]),
    "supervisor":    ("Supervisor",      False, [
        "vehicle.view",
        "entry.view", "entry.create", "entry.edit",
        "maintenance_record.view", "maintenance_record.create",
        "alert.view", "alert.resolve",
        "expense.view", "expense.create",
        "report.view",
    ]),
    "inspector":     ("Inspector",       False, [
        "vehicle.view",
        "entry.view",
        "maintenance_record.view", "maintenance_rule.view",
        "alert.view",
        "expense.view",
        "report.view",
    ]),
    "external":      ("External",        False, [
        "vehicle.view",
        "entry.view",
        "report.view",
    ]),
}


def _seed_system_roles_data():
    """Insert any missing system roles + their RolePermission rows. Idempotent.
    Never overwrites existing role config — admins can re-tune permissions
    via the UI without losing their changes on next deploy.
    """
    created = 0
    for slug, (name, can_approve, perm_keys) in SYSTEM_ROLES.items():
        if Role.query.filter_by(slug=slug).first():
            continue
        role = Role(name=name, slug=slug, is_system=True, can_approve=can_approve)
        db.session.add(role)
        db.session.flush()
        for key in perm_keys:
            p = Permission.query.filter_by(key=key).first()
            if p:
                db.session.add(RolePermission(role_id=role.id, permission_id=p.id, requires_approval=False))
        created += 1
    db.session.commit()
    return created


def _seed_default_categories_data():
    """Insert default vehicle categories + app settings. Idempotent."""
    added = 0
    for code, label, label_fr, unit, baseline, cost, order in DEFAULT_CATEGORIES:
        if not VehicleCategory.query.filter_by(code=code).first():
            db.session.add(VehicleCategory(
                code=code, label=label, label_fr=label_fr, unit_type=unit,
                default_baseline_l_per_unit=baseline, default_cost_per_unit=cost,
                sort_order=order,
            ))
            added += 1
    for key, value, label, category in DEFAULT_SETTINGS:
        if not db.session.get(AppSetting, key):
            db.session.add(AppSetting(key=key, value=value, label=label, category=category))
    db.session.commit()
    return added


@app.cli.command("seed-permissions")
def seed_permissions_cmd():
    """Insert the master permission catalogue. Idempotent."""
    added = 0
    for key, label, category, resource, action in PERMISSIONS_CATALOG:
        if not Permission.query.filter_by(key=key).first():
            db.session.add(Permission(key=key, label=label, category=category,
                                      resource=resource, action=action))
            added += 1
    db.session.commit()
    click.echo(f"Permissions: {added} added, {len(PERMISSIONS_CATALOG)} total in catalogue.")


@app.cli.command("seed-default-categories")
def seed_default_categories_cmd():
    """Seed the default vehicle categories + app settings. Idempotent."""
    added = _seed_default_categories_data()
    click.echo(f"Categories: {added} added. Default settings ensured.")


@app.cli.command("seed-system-roles")
def seed_system_roles_cmd():
    """Seed the system roles: Fleet Manager, Supervisor, Inspector, External.

    Idempotent — never overwrites custom role configuration. If a system
    role already exists, its permissions are NOT reset (so admins can tune
    them and not have the tuning blown away on next deploy).
    """
    created = _seed_system_roles_data()
    click.echo(f"System roles: {created} created (idempotent — existing ones left untouched).")


def _seed_super_admin_from_env():
    """Create the single super admin from ADMIN_USERNAME / ADMIN_PASSWORD env
    vars if no super admin exists yet. Idempotent: silently no-ops on
    redeploys where the super admin row is already there.

    Refuses to start if a brand-new install lacks ADMIN_PASSWORD — better
    to fail loud than to leave the app without an admin.
    """
    if User.query.filter_by(is_super_admin=True).first():
        return False  # already bootstrapped

    username = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not password:
        raise RuntimeError(
            "ADMIN_PASSWORD environment variable is required to bootstrap the super admin."
        )
    if User.query.filter_by(username=username).first():
        raise RuntimeError(
            f"A user with username '{username}' already exists but is not the super admin."
        )
    email = (os.environ.get("ADMIN_EMAIL") or "").strip() or None
    full_name = os.environ.get("ADMIN_FULL_NAME", "Super Admin")

    u = User(
        username=username, full_name=full_name, email=(email.lower() if email else None),
        is_super_admin=True, lang=os.environ.get("APP_DEFAULT_LANG", "fr"),
    )
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return True


@app.cli.command("seed")
def seed_cmd():
    """Bootstrap the whole app: permissions, system roles, default categories,
    settings, and the single super admin from env vars.

    All steps are idempotent — safe to re-run on every deploy.
    """
    # 1. Permissions catalog
    added = 0
    for key, label, category, resource, action in PERMISSIONS_CATALOG:
        if not Permission.query.filter_by(key=key).first():
            db.session.add(Permission(key=key, label=label, category=category,
                                      resource=resource, action=action))
            added += 1
    db.session.commit()
    click.echo(f"  permissions: {added} added, {len(PERMISSIONS_CATALOG)} in catalog")

    # 2. System roles
    role_count = _seed_system_roles_data()
    click.echo(f"  system roles: {role_count} created (existing left untouched)")

    # 3. Default vehicle categories + app settings
    cats_added = _seed_default_categories_data()
    click.echo(f"  categories: {cats_added} added; app settings ensured")

    # 4. Super admin from env vars
    created = _seed_super_admin_from_env()
    if created:
        click.echo(f"  super admin: created from ADMIN_USERNAME env var")
    else:
        click.echo(f"  super admin: already exists, skipped")

    click.echo("Seed complete.")


@app.cli.command("seed-super-admin")
@click.option("--username", required=True, help="login username (no spaces, case-insensitive)")
@click.option("--password", required=True)
@click.option("--full-name", default="Super Admin")
@click.option("--email", default=None, help="optional, for password reset and notifications")
def seed_super_admin_cmd(username, password, full_name, email):
    """Create the single super admin user via CLI flags.

    Use this when you don't want to put credentials in .env (e.g. on a
    shared box). Refuses if one already exists.
    """
    if User.query.filter_by(is_super_admin=True).first():
        click.echo("A super admin already exists.", err=True)
        click.echo("Use `flask reset-super-admin-password` or transfer via SQL.", err=True)
        raise SystemExit(1)
    username = username.strip().lower()
    if User.query.filter_by(username=username).first():
        click.echo(f"A user with username '{username}' already exists.", err=True)
        raise SystemExit(1)
    u = User(
        username=username, full_name=full_name,
        email=(email.strip().lower() if email else None),
        is_super_admin=True, lang=os.environ.get("APP_DEFAULT_LANG", "fr"),
    )
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    click.echo(f"Super admin created: {username}")


@app.cli.command("reset-super-admin-password")
@click.option("--password", required=True)
def reset_super_admin_password_cmd(password):
    """Reset the super admin's password (recovery)."""
    u = User.query.filter_by(is_super_admin=True).first()
    if not u:
        click.echo("No super admin found. Use seed-super-admin first.", err=True)
        raise SystemExit(1)
    u.set_password(password)
    db.session.commit()
    click.echo(f"Super admin password reset for {u.username}.")


@app.cli.command("grant-fleet")
@click.option("--user",  required=True, help="username of the user")
@click.option("--fleet", required=True, help="slug of the fleet")
@click.option("--role",  required=True, help="name or slug of the role")
def grant_fleet_cmd(user, fleet, role):
    """Break-glass: assign a fleet/role to a user from the CLI."""
    u = User.query.filter_by(username=user.lower()).first()
    f = Fleet.query.filter_by(slug=fleet).first()
    r = Role.query.filter_by(slug=role).first() or Role.query.filter_by(name=role).first()
    if not u or not f or not r:
        click.echo(f"Lookup failed: user={bool(u)} fleet={bool(f)} role={bool(r)}", err=True)
        raise SystemExit(1)
    uf = UserFleet.query.filter_by(user_id=u.id, fleet_id=f.id).first()
    if uf:
        uf.role_id = r.id
        action = "Updated"
    else:
        db.session.add(UserFleet(user_id=u.id, fleet_id=f.id, role_id=r.id))
        action = "Created"
    db.session.commit()
    click.echo(f"{action}: {u.username} → {f.name} as {r.name}")


# ── Boot ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
