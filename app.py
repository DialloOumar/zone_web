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
import re
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
                    FleetRate, MaintenanceRecord, MaintenanceRule, Operator,
                    PendingChange, Permission, Role, RolePermission, User,
                    UserFleet, Vehicle, VehicleCategory, db)

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


APP_VERSION = "0.2.1"  # release number, shown to humans


def _static_version():
    """Cache-busting stamp for /static URLs: the newest mtime under static/.

    Derived, not hand-maintained. A manually bumped constant is only as good as
    the memory of whoever edits a stylesheet — forget it once and every browser
    keeps serving the old CSS/JS with no sign anything is wrong. Computed once
    at import (a few ms), so it changes whenever the app restarts on a deploy
    with modified assets.
    """
    newest = 0
    for root, _dirs, files in os.walk(app.static_folder):
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(root, name)).st_mtime)
            except OSError:      # file vanished mid-walk; it just misses the stamp
                pass
    return str(int(newest)) if newest else APP_VERSION


STATIC_VERSION = _static_version()


def current_lang():
    """Pick the language for this request: logged-in user preference, then
    session, then APP_DEFAULT_LANG env, default 'fr'.
    """
    if current_user.is_authenticated and getattr(current_user, "lang", None):
        return current_user.lang
    if session.get("lang"):
        return session["lang"]
    try:
        configured = _get_setting("default_lang", None)
    except Exception:
        configured = None  # DB not migrated yet — fall back to env
    return configured or os.environ.get("APP_DEFAULT_LANG", "fr")


def get_t():
    """Return the translation dict for the current request."""
    lang = current_lang()
    return TRANSLATIONS.get(lang, TRANSLATIONS["fr"])


@app.route("/set-language/<lang>")
def set_language(lang):
    if lang in TRANSLATIONS:
        session["lang"] = lang
        if current_user.is_authenticated:
            current_user.lang = lang
            db.session.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.context_processor
def _inject_globals():
    """Make `t`, `has_perm`, `lang`, `v` callable inside Jinja templates."""
    can_approve_any = current_user.is_authenticated and (
        current_user.is_super_admin or
        any(uf.role and uf.role.can_approve for uf in current_user.user_fleets)
    )
    pending_approvals = 0
    if can_approve_any:
        q = PendingChange.query.filter_by(status="pending")
        if not current_user.is_super_admin:
            fids = [uf.fleet_id for uf in current_user.user_fleets
                    if uf.role and uf.role.can_approve]
            q = q.filter(PendingChange.fleet_id.in_(fids))
        pending_approvals = q.count()
    # The current user's own in-flight requests, for the "Mes demandes" badge.
    my_pending = 0
    if current_user.is_authenticated and not current_user.is_super_admin:
        my_pending = PendingChange.query.filter_by(
            requested_by=current_user.id, status="pending").count()
    # Active maintenance alerts (open only; snoozed are deferred), scoped — for
    # the "Alertes" badge.
    active_alerts = 0
    if current_user.is_authenticated and has_perm("alert.view"):
        aq = Alert.query.filter(Alert.status == "open")
        fids = current_user_fleet_ids()
        if fids is not None:
            aq = (aq.join(Vehicle, Alert.vehicle_id == Vehicle.id)
                    .filter(Vehicle.fleet_id.in_(fids)))
        active_alerts = aq.count()
    return {
        "t": get_t(),
        "lang": current_lang(),
        "v": STATIC_VERSION,
        "has_perm": has_perm,
        "is_super_admin": current_user.is_authenticated and current_user.is_super_admin,
        "can_approve_any": can_approve_any,
        "pending_approvals": pending_approvals,
        "my_pending": my_pending,
        "active_alerts": active_alerts,
        "currency": _get_setting("currency", "GNF"),
        # Billing rates only exist to feed facturation, so they follow it in
        # and out of hiding rather than needing a switch of their own.
        "billing_visible": "invoicing.view" not in HIDDEN_PERMS,
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


def with_current_fleet(fleets, current_fleet):
    """Add a record's own fleet to a form's active-fleet list when that fleet
    is archived — so editing a record that lives on a mothballed fleet keeps
    its fleet selectable (you can't newly assign to an archived fleet, but you
    won't silently move an existing record off it either)."""
    if current_fleet and not current_fleet.is_active and current_fleet not in fleets:
        return fleets + [current_fleet]
    return fleets


# Modules kept in the code but hidden from everyone for now — the seed for the
# future paid tier. A permission listed here reads as "not held" for every user
# (super admin included), which hides its nav entry, onboarding step and help
# section, and 403s its routes. Roles and seed data are left untouched, so
# dropping a key from this set brings the module straight back.
HIDDEN_PERMS = {"invoicing.view"}


def has_perm(perm_key):
    """True if the current user holds the given permission via any of their
    fleet assignments. Super admin always passes. Cached per request.
    """
    if not current_user.is_authenticated:
        return False
    if perm_key in HIDDEN_PERMS:
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


def slugify(text):
    """Lowercase, hyphenate, strip non-word chars — for fleet / role slugs."""
    s = re.sub(r"[^\w\s-]", "", (text or "").strip().lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "item"


def submit_change(*, resource_type, action, fleet_id, payload, resource_id=None, reason=None):
    """Park a change in the approval queue as a PendingChange row.

    Feature modules call this when needs_approval() is True: instead of
    applying the change, the proposed state is stored for a reviewer with
    can_approve on the fleet. The Approvals UI (built later) replays the
    payload on approval. Returns the created row.
    """
    pc = PendingChange(
        requested_by=current_user.id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        payload=payload,
        reason=reason,
        fleet_id=fleet_id,
        status="pending",
    )
    db.session.add(pc)
    log_action("SUBMIT", resource_type, resource_id=resource_id, fleet_id=fleet_id,
               detail=f"Submitted {action} for approval")
    db.session.commit()
    return pc


def pending_change_exists(resource_type, resource_id):
    """True if an unresolved change is already queued for this exact record.

    Used to block duplicate or conflicting edit/delete submissions: a record
    stays locked until its pending change is approved or rejected.
    """
    if not resource_id:
        return False
    return db.session.query(PendingChange.id).filter_by(
        resource_type=resource_type, resource_id=resource_id,
        status="pending").first() is not None


def is_modal_request():
    """True when a request originates from the modal layer (fetch + header).

    Lets form routes serve a bare partial / JSON to the dialog while still
    rendering full pages for direct navigation and no-JS clients.
    """
    return request.headers.get("X-Requested-With") == "fetch"


def modal_ok():
    """Success response for a modal form submit — the client reloads to pick
    up the flash message and the refreshed list."""
    return jsonify(ok=True)


# ── Base routes (auth + landing placeholder) ─────────────────────────────────


# Localized short month labels for the spend-trend chart (index = month - 1).
MONTH_ABBR = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def _months_back(now, n=6):
    """The last n months as 'YYYY-MM', oldest first."""
    out = []
    y, m = now.year, now.month
    for i in range(n - 1, -1, -1):
        yy, mm = y, m - i
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append("%04d-%02d" % (yy, mm))
    return out


def _dashboard_charts(fleet_ids, now, lang):
    """Build the operational dashboard series, fleet-scoped.

    1. activity_trend — worked hours & trips per month over the last 6 months.
    2. daily_entries  — entries logged per day over the last 30 days, which is
       where gaps in the daily logging discipline show up.
    3. top_vehicles   — the busiest machines this month, each in its own unit.
    4. fuel_trend     — litres filled per month over the last 6 months.
    """
    abbr = MONTH_ABBR.get(lang, MONTH_ABBR["en"])
    co = db.func.coalesce

    def scope(q, model=Vehicle):
        return q.filter(model.fleet_id.in_(fleet_ids)) if fleet_ids is not None else q

    months = _months_back(now, 6)
    month_labels = [abbr[int(mo[5:]) - 1] for mo in months]

    # 1. Hours & trips per month.
    ym = db.func.substr(DailyEntry.date, 1, 7)
    q = (db.session.query(ym, co(db.func.sum(DailyEntry.hours), 0.0),
                          co(db.func.sum(DailyEntry.trips), 0))
         .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
         .filter(ym.in_(months)))
    per_month = {mo: (float(h or 0), int(tr or 0)) for mo, h, tr in scope(q).group_by(ym).all()}

    # 2. Entries per day over the last 30 days.
    days = [(now.date() - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    q = (db.session.query(DailyEntry.date, db.func.count(DailyEntry.id))
         .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
         .filter(DailyEntry.date.in_(days)))
    per_day = dict(scope(q).group_by(DailyEntry.date).all())

    # 3. Busiest machines this month — ranked on each one's own unit, and kept
    #    in two series so hours and trips are never drawn as the same thing.
    like = now.strftime("%Y-%m") + "%"
    q = (db.session.query(Vehicle.code, VehicleCategory.unit_type,
                          co(db.func.sum(DailyEntry.hours), 0.0),
                          co(db.func.sum(DailyEntry.trips), 0))
         .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
         .join(VehicleCategory, Vehicle.category_id == VehicleCategory.id)
         .filter(DailyEntry.date.like(like)))
    ranked = []
    for code, unit, hrs, trp in scope(q).group_by(Vehicle.id, Vehicle.code,
                                                  VehicleCategory.unit_type).all():
        value = float(hrs or 0) if unit == "hours" else float(trp or 0)
        if value > 0:
            ranked.append((code, unit, value))
    ranked.sort(key=lambda r: r[2], reverse=True)
    ranked = ranked[:10]

    # 4. Litres filled per month (Expense carries its own fleet_id).
    yme = db.func.substr(Expense.date, 1, 7)
    q = (db.session.query(yme, co(db.func.sum(Expense.liters), 0.0))
         .filter(Expense.category == "fuel", yme.in_(months)))
    per_month_fuel = dict(scope(q, Expense).group_by(yme).all())

    return {
        "activity_trend": {
            "labels": month_labels,
            "hours": [round(per_month.get(mo, (0, 0))[0], 1) for mo in months],
            "trips": [per_month.get(mo, (0, 0))[1] for mo in months],
        },
        "daily_entries": {
            "labels": [d[5:] for d in days],
            "values": [int(per_day.get(d, 0)) for d in days],
        },
        "top_vehicles": {
            "labels": [code for code, _, _ in ranked],
            "hours": [round(val, 1) if unit == "hours" else 0 for _, unit, val in ranked],
            "trips": [int(val) if unit == "trips" else 0 for _, unit, val in ranked],
        },
        "fuel_trend": {
            "labels": month_labels,
            "values": [round(float(per_month_fuel.get(mo, 0) or 0), 1) for mo in months],
        },
    }


def _home_url():
    """Best landing URL for the current user: their dashboard if they may see
    it, otherwise the first page their role can open. Never strands a user on
    a 403 after login."""
    candidates = [
        ("dashboard.view", "dashboard"),
        ("vehicle.view", "vehicles.index"),
        ("entry.view", "entries.roster_index"),
        ("operator.view", "operators.index"),
        ("carburant.view", "carburant.index"),
        ("expense.view", "expenses.index"),
        ("insights.view", "insights.index"),
        ("invoicing.view", "invoicing.index"),
        ("alert.view", "maintenance.alerts"),
        ("maintenance_record.view", "maintenance.records"),
        ("report.view", "entries.index"),
    ]
    for perm, endpoint in candidates:
        if has_perm(perm):
            return url_for(endpoint)
    return url_for("change_password")  # always available to a logged-in user


def _onboarding_steps():
    """The first-run guide for the current user, in setup→ops→piloting order,
    filtered to what their role can actually do. Each step's `done` ticks the
    box when the data already exists (None = a how-to, not a one-time setup)."""
    t = get_t()
    steps = []

    def add(icon, key, endpoint, done=None, **kw):
        steps.append({
            "icon": icon, "done": done,
            "title": t.get("tour.%s.t" % key, key),
            "desc": t.get("tour.%s.d" % key, ""),
            "url": url_for(endpoint, **kw) if endpoint else None,
        })

    # Phase 1 — configuration (super admin only)
    if current_user.is_super_admin:
        add("workspaces", "fleets", "admin.fleets",
            done=Fleet.query.filter_by(is_active=True).count() > 0)
        add("badge", "operators", "operators.index",
            done=Operator.query.filter_by(is_active=True).count() > 0)
        add("directions_bus", "vehicles", "vehicles.index",
            done=Vehicle.query.filter_by(is_active=True).count() > 0)
        add("group", "users", "admin.users",
            done=User.query.filter_by(is_super_admin=False).count() > 0)
        add("rule", "rules", "maintenance.rules",
            done=MaintenanceRule.query.count() > 0)

    # Phase 2 — daily operations
    if has_perm("entry.create"):
        add("fact_check", "entry", "entries.roster_index")
    if has_perm("carburant.create"):
        add("local_gas_station", "carburant", "carburant.index")
    if has_perm("expense.create"):
        add("payments", "expense", "expenses.index")
    if has_perm("maintenance_record.create"):
        add("handyman", "maint", "maintenance.records")
    if has_perm("alert.view"):
        add("warning", "alerts", "maintenance.alerts")

    # Phase 3 — piloting
    if has_perm("dashboard.view"):
        add("space_dashboard", "dashboard", "dashboard")
    if has_perm("insights.view"):
        add("query_stats", "insights", "insights.index")
    if has_perm("invoicing.view"):
        add("request_quote", "invoicing", "invoicing.index")
    return steps


@app.route("/bienvenue")
@login_required
def welcome():
    """Role-based first-login guide (also reachable later via 'Revoir le guide')."""
    return render_template("welcome.html", steps=_onboarding_steps(),
                           first_time=current_user.tour_seen_at is None)


@app.route("/bienvenue/termine", methods=["POST"])
@login_required
def welcome_done():
    if current_user.tour_seen_at is None:
        current_user.tour_seen_at = datetime.utcnow()
        db.session.commit()
    return redirect(_home_url())


@app.route("/aide")
@login_required
def help_page():
    """In-app help, filtered to the topics the current user's access unlocks."""
    from help_content import HELP_SECTIONS

    def _visible(section):
        gate = section["gate"]
        if gate.get("always"):
            return True
        if gate.get("super_admin"):
            return current_user.is_super_admin
        return any(has_perm(p) for p in gate.get("perms", []))

    sections = [s for s in HELP_SECTIONS if _visible(s)]
    # A section is illustrated when static/images/help/<id>.png exists.
    img_dir = os.path.join(app.static_folder, "images", "help")
    have_img = {f[:-4] for f in os.listdir(img_dir)
                if f.endswith(".png")} if os.path.isdir(img_dir) else set()
    return render_template("help.html", sections=sections, have_img=have_img)


@app.route("/")
@login_required
def dashboard():
    """Landing page with at-a-glance KPI cards, scoped to the user's fleets."""
    if not has_perm("dashboard.view"):
        return redirect(_home_url())
    fleet_ids = current_user_fleet_ids()  # None for super admin = no restriction
    vq = Vehicle.query
    oq = Operator.query
    aq = Alert.query.filter(Alert.status == "open")
    if fleet_ids is not None:
        vq = vq.filter(Vehicle.fleet_id.in_(fleet_ids))
        oq = oq.filter(Operator.fleet_id.in_(fleet_ids))
        aq = aq.join(Vehicle, Alert.vehicle_id == Vehicle.id).filter(Vehicle.fleet_id.in_(fleet_ids))
    # This month's operational activity, straight from the daily entries.
    now = datetime.utcnow()
    like = now.strftime("%Y-%m") + "%"
    co = db.func.coalesce
    act = (db.session.query(co(db.func.sum(DailyEntry.hours), 0.0),
                            co(db.func.sum(DailyEntry.trips), 0),
                            co(db.func.sum(DailyEntry.kilometers), 0.0),
                            db.func.count(db.distinct(DailyEntry.vehicle_id)))
           .join(Vehicle, DailyEntry.vehicle_id == Vehicle.id)
           .filter(DailyEntry.date.like(like)))
    if fleet_ids is not None:
        act = act.filter(Vehicle.fleet_id.in_(fleet_ids))
    hours, trips, km, active_vehicles = act.one()

    # Litres filled this month (Expense carries its own fleet_id — no join).
    fq_l = db.session.query(co(db.func.sum(Expense.liters), 0.0)).filter(
        Expense.category == "fuel", Expense.date.like(like))
    if fleet_ids is not None:
        fq_l = fq_l.filter(Expense.fleet_id.in_(fleet_ids))

    stats = {
        "hours": float(hours or 0),
        "trips": int(trips or 0),
        "km": float(km or 0),
        "liters": float(fq_l.scalar() or 0),
        "active_vehicles": int(active_vehicles or 0),
        "vehicles": vq.count(),
        "operators": oq.count(),
        "alerts_open": aq.count(),
    }

    # Open alerts — newest first, scoped via the vehicle's fleet.
    alq = Alert.query.filter(Alert.status == "open")
    if fleet_ids is not None:
        alq = alq.join(Vehicle, Alert.vehicle_id == Vehicle.id).filter(Vehicle.fleet_id.in_(fleet_ids))
    open_alerts = alq.order_by(Alert.triggered_at.desc()).limit(6).all()

    # Recent daily entries — latest operational activity.
    enq = DailyEntry.query
    if fleet_ids is not None:
        enq = enq.join(Vehicle, DailyEntry.vehicle_id == Vehicle.id).filter(Vehicle.fleet_id.in_(fleet_ids))
    recent_entries = enq.order_by(DailyEntry.date.desc(), DailyEntry.id.desc()).limit(8).all()

    charts = _dashboard_charts(fleet_ids, datetime.utcnow(), current_lang())

    return render_template(
        "dashboard.html",
        stats=stats,
        open_alerts=open_alerts,
        recent_entries=recent_entries,
        charts=charts,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_home_url())
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            login_user(user)
            log_action("LOGIN", "auth", detail="Successful login")
            db.session.commit()
            dest = request.args.get("next")
            if not dest and user.tour_seen_at is None:
                dest = url_for("welcome")  # first login → role-based welcome guide
            return redirect(dest or _home_url())
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


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Let any logged-in user change their own password.

    Same shape as batmex_web: translation keys are returned in error/success
    so the template renders the localized message.
    """
    error = success = None
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw     = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not current_user.check_password(current_pw):
            error = "pw.error.wrong_current"
        elif len(new_pw) < 6:
            error = "pw.error.too_short"
        elif new_pw != confirm_pw:
            error = "pw.error.no_match"
        else:
            current_user.set_password(new_pw)
            log_action("UPDATE", "user", resource_id=current_user.id,
                       detail="Self-service password change")
            db.session.commit()
            success = "pw.success"
    return render_template("change_password.html", error=error, success=success)


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
    # Dashboard
    ("dashboard.view", "View dashboard", "Dashboard", "dashboard", "view"),
    # Vehicles
    ("vehicle.view",   "View vehicles",   "Vehicles", "vehicle", "view"),
    ("vehicle.create", "Create vehicle",  "Vehicles", "vehicle", "create"),
    ("vehicle.edit",   "Edit vehicle",    "Vehicles", "vehicle", "edit"),
    ("vehicle.delete", "Delete vehicle",  "Vehicles", "vehicle", "delete"),
    # Operators (drivers)
    ("operator.view",   "View operators",   "Operators", "operator", "view"),
    ("operator.create", "Create operator",  "Operators", "operator", "create"),
    ("operator.edit",   "Edit operator",    "Operators", "operator", "edit"),
    ("operator.delete", "Delete operator",  "Operators", "operator", "delete"),
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
    # Carburant (fuel tankers / distribution)
    ("carburant.view",   "View fuel & citernes",    "Carburant", "carburant", "view"),
    ("carburant.create", "Log a fuel distribution", "Carburant", "carburant", "create"),
    ("carburant.manage", "Manage citernes",         "Carburant", "carburant", "manage"),
    # Insights (analyse)
    ("insights.view", "View insights", "Insights", "insights", "view"),
    # Reports
    ("report.view",       "View reports",            "Reports", "report", "view"),
    # Key kept as-is (it is seeded into the DB and granted to roles); the PDF
    # export it was named for is now a browser print view.
    ("report.export_pdf", "Print / export reports",   "Reports", "report", "export"),
    # Invoicing (facturation)
    ("invoicing.view", "View invoicing", "Invoicing", "invoicing", "view"),
    # Admin (super admin only — these aren't exposed in the role grid, just here for documentation)
    ("admin.users",      "Manage users",      "Administration", "admin", "users"),
    ("admin.fleets",     "Manage fleets",     "Administration", "admin", "fleets"),
    ("admin.roles",      "Manage roles",      "Administration", "admin", "roles"),
    ("admin.categories", "Manage categories", "Administration", "admin", "categories"),
    ("admin.settings",   "Manage settings",   "Administration", "admin", "settings"),
]


# Default vehicle categories — seeded into vehicle_categories.
# Format: (code, label_en, label_fr, unit_type, default_l_per_unit, default_cost_per_unit, sort_order)
# (code, label, label_fr, tracking, baseline, cost, sort_order)
# tracking: "trips" | "hours" | "hours_index"; unit_type is derived from it.
DEFAULT_CATEGORIES = [
    ("BUS",         "Bus",             "Bus",                "trips",        12.0,  None,  1),
    ("MINIBUS",     "Minibus",         "Minibus",            "trips",         8.0,  None,  2),
    ("NAVETTE",     "Shuttle",         "Navette",            "trips",         6.0,  None,  3),
    ("CAMION_TSF",  "TSF Truck",       "Camion TSF",         "hours",        25.0,  None,  4),
    ("MACHINE_TSF", "TSF Machine",     "Machine TSF",        "hours_index",  30.0,  None,  5),
    ("CITERNE",     "Water tanker",    "Citerne à eau",      "trips",        15.0,  None,  6),
    ("SERVICE",     "Service vehicle", "Véhicule de service", "trips",        4.0,  None,  7),
    ("AUTRE",       "Other",           "Autre",              "trips",        None,  None,  8),
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
        "dashboard.view",
        "vehicle.view", "vehicle.create", "vehicle.edit", "vehicle.delete",
        "operator.view", "operator.create", "operator.edit", "operator.delete",
        "entry.view", "entry.create", "entry.edit", "entry.delete",
        "maintenance_record.view", "maintenance_record.create", "maintenance_record.edit", "maintenance_record.delete",
        "maintenance_rule.view", "maintenance_rule.create", "maintenance_rule.edit", "maintenance_rule.delete",
        "alert.view", "alert.resolve", "alert.dismiss",
        "expense.view", "expense.create", "expense.edit", "expense.delete",
        "carburant.view", "carburant.create", "carburant.manage",
        "insights.view", "report.view", "report.export_pdf",
        "invoicing.view",
    ]),
    "supervisor":    ("Supervisor",      False, [
        "dashboard.view",
        "vehicle.view",
        "operator.view",
        "entry.view", "entry.create", "entry.edit",
        "maintenance_record.view", "maintenance_record.create",
        "alert.view", "alert.resolve",
        "expense.view", "expense.create",
        "insights.view", "report.view",
    ]),
    "inspector":     ("Inspector",       False, [
        "dashboard.view",
        "vehicle.view",
        "operator.view",
        "entry.view",
        "maintenance_record.view", "maintenance_rule.view",
        "alert.view",
        "expense.view",
        "insights.view", "report.view",
    ]),
    "external":      ("External",        False, [
        "dashboard.view",
        "vehicle.view",
        "operator.view",
        "entry.view",
        "report.view",
    ]),
}


# Deleting a system role has to survive the next boot: entrypoint.sh runs
# `flask seed` every start, which would otherwise re-create it. Deleted slugs
# are remembered here (category "internal", so /admin/settings ignores them).
SYSTEM_ROLES_TOMBSTONE = "deleted_system_roles"


def tombstoned_system_roles():
    """Slugs of system roles an admin deleted — never re-seed these."""
    row = db.session.get(AppSetting, SYSTEM_ROLES_TOMBSTONE)
    return {s for s in (row.value or "").split(",") if s} if row else set()


def tombstone_system_role(slug):
    """Remember that this system role was deleted on purpose. Caller commits."""
    row = db.session.get(AppSetting, SYSTEM_ROLES_TOMBSTONE)
    slugs = tombstoned_system_roles() | {slug}
    if row:
        row.value = ",".join(sorted(slugs))
    else:
        db.session.add(AppSetting(
            key=SYSTEM_ROLES_TOMBSTONE, value=slug, category="internal",
            label="System roles deleted by an admin (not re-seeded)"))


def _seed_system_roles_data():
    """Insert any missing system roles + their RolePermission rows. Idempotent.
    Never overwrites existing role config — admins can re-tune permissions
    via the UI without losing their changes on next deploy, and roles they
    deleted outright stay deleted.
    """
    created = 0
    deleted = tombstoned_system_roles()
    for slug, (name, can_approve, perm_keys) in SYSTEM_ROLES.items():
        if slug in deleted or Role.query.filter_by(slug=slug).first():
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
    for code, label, label_fr, tracking, baseline, cost, order in DEFAULT_CATEGORIES:
        if not VehicleCategory.query.filter_by(code=code).first():
            db.session.add(VehicleCategory(
                code=code, label=label, label_fr=label_fr,
                tracking=tracking, unit_type=("trips" if tracking == "trips" else "hours"),
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
    gone = tombstoned_system_roles()
    if gone:
        click.echo("  not re-seeded (deleted by an admin): " + ", ".join(sorted(gone)))
        click.echo("  run `flask restore-system-roles` to bring them back.")


@app.cli.command("restore-system-roles")
def restore_system_roles_cmd():
    """Undo system-role deletions: forget the tombstones and re-seed them.

    The escape hatch for `flask seed` deliberately not re-creating a system
    role an admin deleted from /admin/roles.
    """
    row = db.session.get(AppSetting, SYSTEM_ROLES_TOMBSTONE)
    gone = tombstoned_system_roles()
    if not gone:
        click.echo("Nothing to restore — no system role has been deleted.")
        return
    db.session.delete(row)
    db.session.commit()
    created = _seed_system_roles_data()
    click.echo(f"Restored {created} system role(s): {', '.join(sorted(gone))}")


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


# Hard cap: the primary admin + one more (e.g. the client's manager). No more.
MAX_SUPER_ADMINS = 2


@app.cli.command("grant-super-admin")
@click.option("--username", required=True, help="existing user to promote")
def grant_super_admin_cmd(username):
    """Promote an existing user to super admin (e.g. a backup admin account).

    Create the account first via Administration → Users, then promote it here.
    """
    uname = username.strip().lower()
    u = User.query.filter_by(username=uname).first()
    if not u:
        click.echo(f"No user '{uname}'. Create the account first, then promote it.", err=True)
        raise SystemExit(1)
    if u.is_super_admin:
        click.echo(f"'{uname}' is already a super admin.")
        return
    if User.query.filter_by(is_super_admin=True).count() >= MAX_SUPER_ADMINS:
        click.echo(f"Refusing: the maximum of {MAX_SUPER_ADMINS} super admins is already reached. "
                   "Revoke one first.", err=True)
        raise SystemExit(1)
    u.is_super_admin = True
    db.session.commit()
    click.echo(f"'{uname}' is now a super admin.")


@app.cli.command("revoke-super-admin")
@click.option("--username", required=True, help="super admin to demote")
def revoke_super_admin_cmd(username):
    """Remove super-admin status from a user. Refuses to remove the last one.

    The demoted user keeps their account but reverts to a normal user — assign
    them a role per fleet in Administration → Users if they still need access.
    """
    uname = username.strip().lower()
    u = User.query.filter_by(username=uname).first()
    if not u or not u.is_super_admin:
        click.echo(f"'{uname}' is not a super admin.", err=True)
        raise SystemExit(1)
    primary = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
    if uname == primary:
        click.echo(f"Refusing: '{uname}' is the primary super admin and is permanently protected.", err=True)
        raise SystemExit(1)
    if User.query.filter_by(is_super_admin=True).count() <= 1:
        click.echo("Refusing: this is the last super admin. Promote another account first.", err=True)
        raise SystemExit(1)
    u.is_super_admin = False
    db.session.commit()
    click.echo(f"'{uname}' is no longer a super admin.")


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


# Demo fleets: (name, slug, [category codes]). Categories must already be seeded.
DEMO_FLEETS = [
    ("Zone Nord", "zone-nord", ["BUS", "NAVETTE", "SERVICE"]),
    ("Zone Sud",  "zone-sud",  ["CAMION_TSF", "MACHINE_TSF", "CITERNE"]),
]

# Demo vehicles: (code, category code, fleet slug, fuel factor, idle?).
# fuel factor scales consumption vs. baseline so Insights has a story:
# 1.3 = 30% over (loss), 0.8 = under, ~1.0 = normal. idle = no recent entries.
DEMO_VEHICLES = [
    ("BUS-01",     "BUS",         "zone-nord", 1.32, False),
    ("BUS-02",     "BUS",         "zone-nord", 1.04, False),
    ("NAV-07",     "NAVETTE",     "zone-nord", 0.82, False),
    ("SRV-01",     "SERVICE",     "zone-nord", 1.00, True),
    ("CAM-TSF-03", "CAMION_TSF",  "zone-sud",  1.06, False),
    ("MAC-TSF-01", "MACHINE_TSF", "zone-sud",  0.97, False),
    ("CIT-05",     "CITERNE",     "zone-sud",  1.10, False),
]

DEMO_OPERATORS = {
    "zone-nord": ["Mamadou Diallo", "Aïssatou Bah", "Ousmane Camara"],
    "zone-sud":  ["Saïkou Barry", "Fatoumata Sow", "Ibrahima Touré"],
}

FUEL_PRICE_GNF = 13_000  # per liter


@app.cli.command("seed-demo")
@click.option("--days", default=80, help="how many past days of activity to generate")
@click.option("--force", is_flag=True, help="proceed even if demo data already exists")
def seed_demo_cmd(days, force):
    """Populate realistic operational demo data (fleets, vehicles, operators,
    daily entries, fuel/other expenses, maintenance rules + records, alerts).

    Built so the dashboard and Insights tell a story: one vehicle consistently
    over its fuel baseline (loss), one under, one idle, plus open alerts and
    six months of spend. Idempotent on stable keys (fleet slug, vehicle code,
    operator name); safe to re-run, but new dated rows accumulate, so use a
    fresh DB for a clean demo.
    """
    import random
    from datetime import date, timedelta
    rng = random.Random(42)  # deterministic

    if Vehicle.query.first() and not force:
        click.echo("Vehicles already exist. Re-run with --force to add demo data anyway.", err=True)
        raise SystemExit(1)

    _seed_default_categories_data()
    cats = {c.code: c for c in VehicleCategory.query.all()}
    if not cats:
        click.echo("No vehicle categories found — run `flask seed` first.", err=True)
        raise SystemExit(1)

    # Fleets
    fleets = {}
    for name, slug, cat_codes in DEMO_FLEETS:
        f = Fleet.query.filter_by(slug=slug).first()
        if not f:
            f = Fleet(name=name, slug=slug, categories=cat_codes)
            db.session.add(f)
        fleets[slug] = f
    db.session.flush()

    # Operators
    for slug, names in DEMO_OPERATORS.items():
        for nm in names:
            if not Operator.query.filter_by(fleet_id=fleets[slug].id, name=nm).first():
                db.session.add(Operator(fleet_id=fleets[slug].id, name=nm, is_active=True))
    db.session.flush()
    ops_by_fleet = {
        slug: [o.name for o in Operator.query.filter_by(fleet_id=fleets[slug].id).all()]
        for slug in fleets
    }
    # First operator of each fleet — used as the vehicles' default driver.
    first_op = {
        slug: Operator.query.filter_by(fleet_id=fleets[slug].id).order_by(Operator.id).first()
        for slug in fleets
    }

    # Vehicles
    vehicles = {}
    for code, cat_code, slug, factor, idle in DEMO_VEHICLES:
        v = Vehicle.query.filter_by(code=code).first()
        if not v:
            v = Vehicle(code=code, category_id=cats[cat_code].id, fleet_id=fleets[slug].id, is_active=True,
                        default_operator_id=(first_op[slug].id if first_op.get(slug) else None))
            db.session.add(v)
        vehicles[code] = (v, cat_code, slug, factor, idle)
    db.session.flush()

    today = date.today()
    start = today - timedelta(days=days)

    # Daily entries + running cumulative; tally units & km per (vehicle, month).
    units_vm, km_vm = {}, {}
    cum_km, cum_h, meter = {}, {}, {}
    for code, (v, cat_code, slug, factor, idle) in vehicles.items():
        cat = cats[cat_code]
        cum_km[code] = 0.0
        cum_h[code] = 0.0
        meter[code] = float(rng.randint(800, 2500))  # hour-meter base for index machines
        d = start
        while d <= today:
            # SRV-01 (idle) stops logging 22 days ago; everyone skips ~weekends/random days.
            recent_cut = today - timedelta(days=22)
            skip = (idle and d > recent_cut) or rng.random() < 0.25
            if not skip:
                ym = d.strftime("%Y-%m")
                op = rng.choice(ops_by_fleet[slug]) if ops_by_fleet[slug] else None
                km = round(rng.uniform(40, 120) if cat.unit_type == "trips" else rng.uniform(0, 30), 0)
                cum_km[code] += km
                trips = hours = index_start = index_end = None
                if cat.unit_type == "trips":
                    trips = rng.randint(4, 16)
                    units_vm[(code, ym)] = units_vm.get((code, ym), 0) + trips
                else:
                    hours = round(rng.uniform(5, 11), 1)
                    if cat.tracking == "hours_index":
                        index_start = round(meter[code], 1)
                        index_end = round(meter[code] + hours, 1)
                        meter[code] = index_end
                    cum_h[code] += hours
                    units_vm[(code, ym)] = units_vm.get((code, ym), 0) + hours
                km_vm[(code, ym)] = km_vm.get((code, ym), 0) + km
                db.session.add(DailyEntry(
                    vehicle_id=v.id, date=d.strftime("%Y-%m-%d"),
                    trips=trips, hours=hours, kilometers=km,
                    index_start=index_start, index_end=index_end,
                    cumulative_km=round(cum_km[code], 1),
                    cumulative_hours=round(cum_h[code], 1) if cat.unit_type == "hours" else None,
                    operator=op,
                ))
            d += timedelta(days=1)
    db.session.flush()

    # Fuel expenses — one per (vehicle, month), coherent with logged activity,
    # scaled by the vehicle's fuel factor so Insights variance is meaningful.
    for code, (v, cat_code, slug, factor, idle) in vehicles.items():
        baseline = cats[cat_code].default_baseline_l_per_unit or 10.0
        months = sorted({ym for (c, ym) in units_vm if c == code})
        for ym in months:
            units = units_vm[(code, ym)]
            liters = round(units * baseline * factor * rng.uniform(0.96, 1.04), 0)
            if liters <= 0:
                continue
            db.session.add(Expense(
                vehicle_id=v.id, fleet_id=v.fleet_id, category="fuel",
                date=ym + "-08", amount=int(liters * FUEL_PRICE_GNF), liters=liters,
                supplier="Total Énergies",
            ))

    # A few non-fuel expenses this month for the cost split.
    this_month = today.strftime("%Y-%m")
    extra = [
        ("BUS-01",     "autre",     1_500_000, "Assurance trimestrielle"),
        ("CAM-TSF-03", "accident",    900_000, "Pare-brise remplacé"),
        ("NAV-07",     "lavage",      150_000, None),
        ("CIT-05",     "autre",       300_000, "Frais de mission"),
    ]
    for code, cat_c, amt, desc in extra:
        v = vehicles[code][0]
        db.session.add(Expense(
            vehicle_id=v.id, fleet_id=v.fleet_id, category=cat_c,
            date=this_month + "-12", amount=amt, description=desc,
        ))

    # Maintenance rules
    rules = {}
    rule_specs = [
        ("Vidange 5000 km", "km_recurring",    5000, "warning",  "zone-nord", "oil_change"),
        ("Révision 250 h",  "hours_recurring",  250, "critical", "zone-sud",  "inspection"),
    ]
    for name, rtype, interval, sev, slug, stype in rule_specs:
        r = MaintenanceRule.query.filter_by(name=name).first()
        if not r:
            r = MaintenanceRule(name=name, type=rtype, interval=interval, severity=sev,
                                fleet_id=fleets[slug].id, service_type=stype, is_active=True)
            db.session.add(r)
        rules[name] = r
    db.session.flush()

    # Maintenance records (past services). The cost itself lives in the money
    # ledger as the record's linked 'entretien' expense — same as what the
    # service form does — so `record.cost` reads back through it.
    rec_specs = [
        ("BUS-02",     "oil_change", 18, 1_050_000, "Vidange + filtre",  "cash"),
        ("MAC-TSF-01", "inspection", 30, 2_400_000, "Révision 250h",     "transfer"),
        ("CIT-05",     "tires",      45, 1_800_000, "2 pneus avant",     "mobile_money"),
    ]
    for code, rtype, days_ago, cost, desc, pay in rec_specs:
        v = vehicles[code][0]
        rec = MaintenanceRecord(
            vehicle_id=v.id, type=rtype,
            date=(today - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            description=desc, supplier="Garage Central",
            kilometers_at=round(cum_km.get(code, 0), 0),
        )
        db.session.add(rec)
        db.session.flush()
        db.session.add(Expense(
            maintenance_record_id=rec.id, vehicle_id=v.id, fleet_id=v.fleet_id,
            category="entretien", date=rec.date, amount=cost, currency="GNF",
            payment_method=pay, supplier=rec.supplier, description=desc,
        ))

    # Open alerts — the forward-looking "needs attention" list.
    alert_specs = [
        ("Vidange 5000 km", "BUS-01",     "warning",  "Vidange due (5 200 km depuis le dernier entretien)"),
        ("Révision 250 h",  "CAM-TSF-03", "critical", "Révision moteur en retard (268 h)"),
    ]
    for rule_name, code, sev, msg in alert_specs:
        v = vehicles[code][0]
        db.session.add(Alert(
            rule_id=rules[rule_name].id, vehicle_id=v.id,
            severity=sev, message=msg, status="open",
            triggered_at=datetime.utcnow() - timedelta(days=2),
        ))

    db.session.commit()

    # One demo user per system role so each onboarding flow can be tried.
    # (username, full name, password, role slug, fleet slug)
    demo_users = [
        ("manager",    "Chef Zone Nord",  "manager123",    "fleet_manager", "zone-nord"),
        ("supervisor", "Superviseur Sud", "supervisor123", "supervisor",    "zone-sud"),
        ("inspector",  "Inspecteur",      "inspector123",  "inspector",     "zone-nord"),
        ("external",   "Client externe",  "external123",   "external",      "zone-nord"),
    ]
    for username, full_name, password, role_slug, fleet_slug in demo_users:
        role = Role.query.filter_by(slug=role_slug).first()
        if not role or fleet_slug not in fleets or User.query.filter_by(username=username).first():
            continue
        u = User(username=username, full_name=full_name, is_super_admin=False, lang="fr")
        u.set_password(password)
        db.session.add(u)
        db.session.flush()
        db.session.add(UserFleet(user_id=u.id, fleet_id=fleets[fleet_slug].id, role_id=role.id))
        click.echo("  demo user: %s / %s (%s)" % (username, password, role.name))
    db.session.commit()

    click.echo(
        "Demo data seeded: %d fleets, %d vehicles, %d operators, %d daily entries, "
        "%d expenses, %d maintenance records, %d alerts."
        % (Fleet.query.count(), Vehicle.query.count(), Operator.query.count(),
           DailyEntry.query.count(), Expense.query.count(),
           MaintenanceRecord.query.count(), Alert.query.count())
    )


@app.cli.command("wipe-demo")
@click.option("--yes", is_flag=True, help="skip the confirmation prompt")
def wipe_demo_cmd(yes):
    """Permanently remove the demo data created by `seed-demo`.

    Deletes the demo fleets (Zone Nord / Zone Sud) and everything under
    them — vehicles, operators, daily entries, expenses, maintenance
    rules/records, alerts, billing rates — plus the demo 'manager' user.
    Only the demo fleets are touched; any other (real) fleet and its data
    are left intact. This is a HARD delete, not an archive.
    """
    demo_slugs = [slug for _, slug, _ in DEMO_FLEETS]
    fleets = Fleet.query.filter(Fleet.slug.in_(demo_slugs)).all()
    fleet_ids = [f.id for f in fleets]
    vehicle_ids = [v.id for v in Vehicle.query.filter(Vehicle.fleet_id.in_(fleet_ids)).all()] if fleet_ids else []
    mgr = User.query.filter_by(username="manager", is_super_admin=False).first()

    if not fleet_ids and not mgr:
        click.echo("No demo data found — nothing to wipe.")
        return

    if not yes:
        click.echo("This permanently deletes %d demo fleet(s), %d vehicle(s) and all their "
                   "entries/expenses/records%s." %
                   (len(fleet_ids), len(vehicle_ids),
                    " + the demo 'manager' user" if mgr else ""))
        click.confirm("Proceed?", abort=True)

    d = lambda q: q.delete(synchronize_session=False)
    if vehicle_ids:
        d(DailyEntry.query.filter(DailyEntry.vehicle_id.in_(vehicle_ids)))
        d(Alert.query.filter(Alert.vehicle_id.in_(vehicle_ids)))
    if fleet_ids:
        # Expenses / rules / records / rates are keyed by fleet (or its vehicles).
        d(Expense.query.filter(Expense.fleet_id.in_(fleet_ids)))
        if vehicle_ids:
            d(MaintenanceRecord.query.filter(MaintenanceRecord.vehicle_id.in_(vehicle_ids)))
        d(MaintenanceRule.query.filter(MaintenanceRule.fleet_id.in_(fleet_ids)))
        d(FleetRate.query.filter(FleetRate.fleet_id.in_(fleet_ids)))
        d(Vehicle.query.filter(Vehicle.fleet_id.in_(fleet_ids)))      # before operators (FK)
        d(Operator.query.filter(Operator.fleet_id.in_(fleet_ids)))
        d(UserFleet.query.filter(UserFleet.fleet_id.in_(fleet_ids)))
        d(Fleet.query.filter(Fleet.id.in_(fleet_ids)))
    if mgr:
        d(UserFleet.query.filter_by(user_id=mgr.id))
        db.session.delete(mgr)
    db.session.commit()
    click.echo("Demo data wiped.")


# ── Blueprints ───────────────────────────────────────────────────────────────
# Imported here, after the helpers and `app` are defined, so blueprint modules
# can `from app import ...` without tripping a circular import. Each feature
# module (admin, vehicles, entries, …) registers as its own blueprint.
from blueprints.admin import admin_bp  # noqa: E402
from blueprints.entries import entries_bp  # noqa: E402
from blueprints.expenses import expenses_bp  # noqa: E402
from blueprints.carburant import carburant_bp  # noqa: E402
from blueprints.maintenance import maintenance_bp  # noqa: E402
from blueprints.operators import operators_bp  # noqa: E402
from blueprints.vehicles import vehicles_bp  # noqa: E402
from blueprints.approvals import approvals_bp  # noqa: E402  (imports entries/maintenance)
from blueprints.insights import insights_bp  # noqa: E402
from blueprints.invoicing import invoicing_bp  # noqa: E402

app.register_blueprint(admin_bp)
app.register_blueprint(vehicles_bp)
app.register_blueprint(operators_bp)
app.register_blueprint(entries_bp)
app.register_blueprint(expenses_bp)
app.register_blueprint(carburant_bp)
app.register_blueprint(maintenance_bp)
app.register_blueprint(approvals_bp)
app.register_blueprint(insights_bp)
app.register_blueprint(invoicing_bp)


# ── Boot ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
