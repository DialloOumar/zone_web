"""SQLAlchemy models for zone_web.

Schema overview (17 tables, grouped):

  Auth & access     User, Fleet, UserFleet, Role, Permission, RolePermission
  Domain            VehicleCategory, Vehicle
  Daily ops         DailyEntry
  Maintenance       MaintenanceRule, MaintenanceRecord, Alert
  Money             Expense
  Workflow          PendingChange, AuditLog
  Config            AppSetting
"""
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


# ── Auth & access control ─────────────────────────────────────────────────────


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.UniqueConstraint("username", name="uq_users_username"),
        # email is optional and only used for password reset / notifications.
        # When set, it must still be unique so we don't have two users sharing
        # a reset address; the partial uniqueness is handled at insert time.
    )

    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(80),  nullable=False)
    full_name      = db.Column(db.String(120), nullable=False)
    email          = db.Column(db.String(120), nullable=True)            # optional
    password_hash  = db.Column(db.String(256), nullable=False)
    # The one super admin flag — exactly one row in the table has it set to True.
    # Bootstrapped via the seed-super-admin CLI command, never editable in the UI.
    is_super_admin = db.Column(db.Boolean, nullable=False, default=False)
    lang           = db.Column(db.String(5),  nullable=False, default="fr")
    is_active      = db.Column(db.Boolean,    nullable=False, default=True)
    created_at     = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)

    user_fleets = db.relationship("UserFleet", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Fleet(db.Model):
    """An admin-defined logical grouping of vehicle categories.

    Fleets are how staff access is scoped — a UserFleet row grants a user a
    role inside one fleet. Categories belong to exactly one fleet for now;
    a category list lives in `categories` as JSON for flexibility.
    """
    __tablename__ = "fleets"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80),  nullable=False, unique=True)
    slug        = db.Column(db.String(40),  nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    # JSON list of VehicleCategory.code strings, e.g. ["BUS", "MINIBUS", "NAVETTE"]
    categories  = db.Column(db.JSON,        nullable=False, default=list)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


class Role(db.Model):
    """A bundle of permissions, optionally with approval power.

    is_system roles ship with the app and cannot be deleted; custom roles
    can be created on the fly via /admin/roles.
    can_approve grants reviewer power on the assigned fleet (replaces the
    older "is_admin" naming to keep the word "admin" exclusively for
    super admin).
    """
    __tablename__ = "roles"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(80),  nullable=False, unique=True)
    slug        = db.Column(db.String(40),  nullable=False, unique=True)
    description = db.Column(db.String(255), nullable=True)
    is_system   = db.Column(db.Boolean,     nullable=False, default=False)
    can_approve = db.Column(db.Boolean,     nullable=False, default=False)
    created_by  = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    role_permissions = db.relationship("RolePermission", back_populates="role", cascade="all, delete-orphan")


class Permission(db.Model):
    """Master catalogue of permissible (resource × action) pairs.

    Seeded by `flask seed-permissions` from a fixed list in app.py — never
    edited by users. The role config UI groups them by `category` for the
    permission grid layout.
    """
    __tablename__ = "permissions"

    id       = db.Column(db.Integer, primary_key=True)
    key      = db.Column(db.String(64),  nullable=False, unique=True)  # e.g. "vehicle.edit"
    label    = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(50),  nullable=False)               # UI group: "Vehicles", "Reports", "Admin"
    resource = db.Column(db.String(40),  nullable=False)               # "vehicle"
    action   = db.Column(db.String(40),  nullable=False)               # "edit"


class RolePermission(db.Model):
    """Join table: which permissions a role has, and whether they're direct
    or require approval.

    Absence of a row means "Cannot" — the action is forbidden.
    requires_approval=False → Direct (action applies immediately).
    requires_approval=True  → Approval (action queues into PendingChange).
    """
    __tablename__ = "role_permissions"

    role_id           = db.Column(db.Integer, db.ForeignKey("roles.id"),       primary_key=True)
    permission_id     = db.Column(db.Integer, db.ForeignKey("permissions.id"), primary_key=True)
    requires_approval = db.Column(db.Boolean, nullable=False, default=False)

    role       = db.relationship("Role",       back_populates="role_permissions")
    permission = db.relationship("Permission")


class UserFleet(db.Model):
    """Who has which role in which fleet."""
    __tablename__ = "user_fleets"

    user_id     = db.Column(db.Integer, db.ForeignKey("users.id"),  primary_key=True)
    fleet_id    = db.Column(db.Integer, db.ForeignKey("fleets.id"), primary_key=True)
    role_id     = db.Column(db.Integer, db.ForeignKey("roles.id"),  nullable=False)
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    user  = db.relationship("User",  back_populates="user_fleets", foreign_keys=[user_id])
    fleet = db.relationship("Fleet")
    role  = db.relationship("Role")


# ── Domain ────────────────────────────────────────────────────────────────────


class VehicleCategory(db.Model):
    """Master list of vehicle types (Bus, Minibus, Camion TSF, …).

    Each category fixes the unit (`trips` for passenger / transport, `hours`
    for trucks and machines) and a default baseline fuel consumption per
    unit. Individual vehicles can override via Vehicle.baseline_l_per_unit_override.
    """
    __tablename__ = "vehicle_categories"

    id                          = db.Column(db.Integer, primary_key=True)
    code                        = db.Column(db.String(30),  nullable=False, unique=True)  # "BUS", "CAMION_TSF"
    label                       = db.Column(db.String(80),  nullable=False)               # EN label
    label_fr                    = db.Column(db.String(80),  nullable=False)               # FR label
    unit_type                   = db.Column(db.String(10),  nullable=False)               # "trips" | "hours"
    default_baseline_l_per_unit = db.Column(db.Float,       nullable=True)                # L/trip or L/hour
    default_cost_per_unit       = db.Column(db.Integer,     nullable=True)                # GNF per unit, for perte d'exploitation
    icon                        = db.Column(db.String(30),  nullable=True)
    sort_order                  = db.Column(db.Integer,     nullable=False, default=0)


class Vehicle(db.Model):
    __tablename__ = "vehicles"
    __table_args__ = (db.UniqueConstraint("code", name="uq_vehicles_code"),)

    id                            = db.Column(db.Integer, primary_key=True)
    code                          = db.Column(db.String(30), nullable=False)
    category_id                   = db.Column(db.Integer,   db.ForeignKey("vehicle_categories.id"), nullable=False)
    fleet_id                      = db.Column(db.Integer,   db.ForeignKey("fleets.id"),             nullable=False)
    description                   = db.Column(db.String(200), nullable=True)
    site                          = db.Column(db.String(80),  nullable=True)
    baseline_l_per_unit_override  = db.Column(db.Float,     nullable=True)
    cost_per_unit_override        = db.Column(db.Integer,   nullable=True)
    operator_morning              = db.Column(db.String(120), nullable=True)
    operator_evening              = db.Column(db.String(120), nullable=True)
    is_active                     = db.Column(db.Boolean,   nullable=False, default=True)
    created_at                    = db.Column(db.DateTime,  nullable=False, default=datetime.utcnow)
    created_by                    = db.Column(db.Integer,   db.ForeignKey("users.id"), nullable=True)

    category = db.relationship("VehicleCategory")
    fleet    = db.relationship("Fleet")

    @property
    def effective_baseline(self):
        return self.baseline_l_per_unit_override or (self.category and self.category.default_baseline_l_per_unit)

    @property
    def effective_cost_per_unit(self):
        return self.cost_per_unit_override or (self.category and self.category.default_cost_per_unit)


# ── Daily operations ──────────────────────────────────────────────────────────


class DailyEntry(db.Model):
    """One operational period for a vehicle (trip, shift, hour block).

    Multiple rows per (vehicle, date) are allowed — a bus running 4 trips
    with 2 drivers means 4 separate rows. The cumulative columns are
    auto-maintained snapshots used by the maintenance rule engine to
    avoid re-summing on every alert evaluation.
    """
    __tablename__ = "daily_entries"

    id               = db.Column(db.Integer, primary_key=True)
    vehicle_id       = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=False)
    date             = db.Column(db.String(10), nullable=False)              # YYYY-MM-DD
    fuel_liters      = db.Column(db.Float, nullable=True)
    trips            = db.Column(db.Integer, nullable=True)                  # for trip-tracked vehicles
    hours            = db.Column(db.Float,   nullable=True)                  # for hour-tracked vehicles
    kilometers       = db.Column(db.Float,   nullable=True)
    # Running totals as of this entry — for the maintenance rule engine.
    cumulative_km    = db.Column(db.Float,   nullable=True)
    cumulative_hours = db.Column(db.Float,   nullable=True)
    operator         = db.Column(db.String(120), nullable=True)
    note             = db.Column(db.String(255), nullable=True)
    photo_key        = db.Column(db.String(200), nullable=True)              # S3 slip photo
    created_by       = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at       = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle")


# ── Maintenance — rules, records, alerts ──────────────────────────────────────


class MaintenanceRule(db.Model):
    """Trigger definition for proactive maintenance and anomaly alerts."""
    __tablename__ = "maintenance_rules"

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), nullable=False)
    description     = db.Column(db.String(255), nullable=True)
    type            = db.Column(db.String(20),  nullable=False)              # km_recurring | hours_recurring | time_recurring | anomaly
    interval        = db.Column(db.Integer,     nullable=True)               # km, hours, or days
    advance_warning = db.Column(db.Integer,     nullable=True)               # fire N units before threshold

    # Targeting — populate exactly one, or set all_vehicles=True
    vehicle_id   = db.Column(db.Integer, db.ForeignKey("vehicles.id"),            nullable=True)
    category_id  = db.Column(db.Integer, db.ForeignKey("vehicle_categories.id"), nullable=True)
    fleet_id     = db.Column(db.Integer, db.ForeignKey("fleets.id"),             nullable=True)
    all_vehicles = db.Column(db.Boolean, nullable=False, default=False)

    # Anomaly type only
    anomaly_kind   = db.Column(db.String(50),  nullable=True)
    anomaly_params = db.Column(db.JSON,        nullable=True)

    severity         = db.Column(db.String(20),  nullable=False, default="warning")
    notify_in_app    = db.Column(db.Boolean,     nullable=False, default=True)
    notify_email     = db.Column(db.Boolean,     nullable=False, default=False)
    notify_whatsapp  = db.Column(db.Boolean,     nullable=False, default=False)
    is_active        = db.Column(db.Boolean,     nullable=False, default=True)
    created_by       = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at       = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


class MaintenanceRecord(db.Model):
    """A maintenance event that actually happened — closes the relevant alert
    and resets that rule's counter for the vehicle.

    rule_id is nullable so admins can log ad-hoc maintenance that wasn't
    triggered by a rule. cost is duplicated on Expense when an expense
    is auto-linked — the canonical money record is the Expense; this
    column is convenience for the rule-engine math.
    """
    __tablename__ = "maintenance_records"

    id            = db.Column(db.Integer, primary_key=True)
    vehicle_id    = db.Column(db.Integer, db.ForeignKey("vehicles.id"),          nullable=False)
    rule_id       = db.Column(db.Integer, db.ForeignKey("maintenance_rules.id"), nullable=True)
    type          = db.Column(db.String(40),  nullable=False)                    # oil_change | filter | tires | …
    date          = db.Column(db.String(10),  nullable=False)
    kilometers_at = db.Column(db.Float,       nullable=True)
    hours_at      = db.Column(db.Float,       nullable=True)
    cost          = db.Column(db.Integer,     nullable=True)
    supplier      = db.Column(db.String(120), nullable=True)
    description   = db.Column(db.String(255), nullable=True)
    photo_key     = db.Column(db.String(200), nullable=True)
    recorded_by   = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    recorded_at   = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle")
    rule    = db.relationship("MaintenanceRule")


class Alert(db.Model):
    __tablename__ = "alerts"

    id            = db.Column(db.Integer, primary_key=True)
    rule_id       = db.Column(db.Integer, db.ForeignKey("maintenance_rules.id"), nullable=False)
    vehicle_id    = db.Column(db.Integer, db.ForeignKey("vehicles.id"),          nullable=False)
    triggered_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    severity      = db.Column(db.String(20),  nullable=False)
    message       = db.Column(db.String(255), nullable=False)

    status                       = db.Column(db.String(20), nullable=False, default="open")  # open | snoozed | resolved | dismissed
    snoozed_until                = db.Column(db.DateTime, nullable=True)
    resolved_at                  = db.Column(db.DateTime, nullable=True)
    resolved_by                  = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    resolution_note              = db.Column(db.String(255), nullable=True)
    resolution_maintenance_id    = db.Column(db.Integer,  db.ForeignKey("maintenance_records.id"), nullable=True)


# ── Expenses ──────────────────────────────────────────────────────────────────


class Expense(db.Model):
    """All cost-bearing events. Fuel entries can also live here when fuel
    is purchased separately from a daily log — the daily-entry liters and
    expense liters should not be double-counted on the dashboard
    (handled in the aggregation layer).
    """
    __tablename__ = "expenses"

    id                    = db.Column(db.Integer, primary_key=True)
    vehicle_id            = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True)  # null = fleet-wide
    fleet_id              = db.Column(db.Integer, db.ForeignKey("fleets.id"),   nullable=False)  # denormalized for scoping
    category              = db.Column(db.String(40), nullable=False)                              # fuel | vidange | pneus_changement | assurance | …
    date                  = db.Column(db.String(10), nullable=False)
    amount                = db.Column(db.Integer,    nullable=False)                              # GNF
    liters                = db.Column(db.Float,      nullable=True)                               # for fuel category
    currency              = db.Column(db.String(5),  nullable=False, default="GNF")
    supplier              = db.Column(db.String(120), nullable=True)
    description           = db.Column(db.String(255), nullable=True)
    photo_key             = db.Column(db.String(200), nullable=True)                              # S3 receipt
    is_backchargeable     = db.Column(db.Boolean,    nullable=False, default=False)

    # Cross-link when an expense IS a maintenance event (auto-closes the alert)
    maintenance_record_id = db.Column(db.Integer, db.ForeignKey("maintenance_records.id"), nullable=True)

    created_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


# ── Workflow — approvals & audit ──────────────────────────────────────────────


class PendingChange(db.Model):
    """A change request awaiting review by a user with can_approve on the
    relevant fleet.
    """
    __tablename__ = "pending_changes"

    id            = db.Column(db.Integer, primary_key=True)
    requested_by  = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    requested_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    resource_type = db.Column(db.String(40), nullable=False)        # vehicle | daily_entry | maintenance_record | expense | fuel_distribution
    resource_id   = db.Column(db.Integer,    nullable=True)         # null for creates
    action        = db.Column(db.String(20), nullable=False)        # create | update | delete
    payload       = db.Column(db.JSON,       nullable=True)         # proposed state, empty for deletes
    reason        = db.Column(db.String(255), nullable=True)

    status         = db.Column(db.String(20), nullable=False, default="pending")  # pending | approved | rejected | cancelled
    reviewed_by    = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    reviewed_at    = db.Column(db.DateTime, nullable=True)
    review_note    = db.Column(db.String(255), nullable=True)

    fleet_id = db.Column(db.Integer, db.ForeignKey("fleets.id"), nullable=True)  # denormalized for queue scoping


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id            = db.Column(db.Integer, primary_key=True)
    timestamp     = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user_id       = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    username      = db.Column(db.String(120), nullable=False)        # snapshot, survives user rename
    actor_role    = db.Column(db.String(80),  nullable=True)         # snapshot of role at time of action
    fleet_id      = db.Column(db.Integer, db.ForeignKey("fleets.id"), nullable=True)

    action        = db.Column(db.String(30), nullable=False)         # CREATE | UPDATE | DELETE | APPROVE | REJECT | LOGIN | …
    resource_type = db.Column(db.String(40), nullable=False)
    resource_id   = db.Column(db.Integer, nullable=True)
    detail        = db.Column(db.String(255), nullable=True)
    ip_address    = db.Column(db.String(45),  nullable=True)


# ── Config & system ───────────────────────────────────────────────────────────


class AppSetting(db.Model):
    """Singleton-style key/value settings (grace_period_minutes, etc.)
    edited from /admin/settings by super admin only.
    """
    __tablename__ = "app_settings"

    key        = db.Column(db.String(50), primary_key=True)
    value      = db.Column(db.String(255), nullable=False)
    label      = db.Column(db.String(120), nullable=False)
    category   = db.Column(db.String(40),  nullable=False, default="general")
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
