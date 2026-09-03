"""SQLAlchemy models for zone_web.

Schema overview (23 tables, grouped):

  Auth & access     User, Fleet, FleetRate, UserFleet, Role, Permission,
                    RolePermission
  Domain            VehicleCategory, Vehicle, Operator
  Daily ops         DailyEntry
  Maintenance       MaintenanceRule, MaintenanceRecord, Alert
  Money             Expense, CashMovement
  Fuel              Citerne, FuelMovement
  Parts store       Part, StockMovement
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
    # When the user finished/skipped the first-login welcome guide (null = not yet).
    tour_seen_at   = db.Column(db.DateTime,   nullable=True)
    created_at     = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)

    # UserFleet has two FKs pointing at users (user_id and assigned_by), so the
    # back relationship must say which one is the "owner" side.
    user_fleets = db.relationship(
        "UserFleet",
        back_populates="user",
        foreign_keys="UserFleet.user_id",
        cascade="all, delete-orphan",
    )

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
    is_active   = db.Column(db.Boolean,     nullable=False, default=True)  # soft delete = archive
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


class FleetRate(db.Model):
    """Dated billing rate (GNF per worked unit) for a (fleet/client × category).

    A fleet models the client, so two clients can bill different rates for the
    same machine type. Rates are append-only history: changing a price inserts
    a new row with a new effective_from instead of overwriting, so a past month
    can be re-billed at the price that applied then. The rate in force on a date
    is the row with the greatest effective_from <= that date.
    """
    __tablename__ = "fleet_rates"

    id             = db.Column(db.Integer,    primary_key=True)
    fleet_id       = db.Column(db.Integer,    db.ForeignKey("fleets.id"), nullable=False)
    category_code  = db.Column(db.String(30), nullable=False)
    rate_per_unit  = db.Column(db.Integer,    nullable=False)              # GNF per trip / hour
    effective_from = db.Column(db.String(10), nullable=False)             # YYYY-MM-DD
    created_at     = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    created_by     = db.Column(db.Integer,    db.ForeignKey("users.id"), nullable=True)

    fleet = db.relationship("Fleet")


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
    is_active   = db.Column(db.Boolean,     nullable=False, default=True)  # soft delete = archive
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
    unit_type                   = db.Column(db.String(10),  nullable=False)               # "trips" | "hours" (derived from tracking)
    # How a daily entry is logged for this category — chosen at creation:
    #   "trips"        → enter a trip count
    #   "hours"        → enter hours worked directly
    #   "hours_index"  → enter hour-meter index start/end; hours = end - start
    tracking                    = db.Column(db.String(20),  nullable=False, default="trips")
    default_baseline_l_per_unit = db.Column(db.Float,       nullable=True)                # L/trip or L/hour
    default_cost_per_unit       = db.Column(db.Integer,     nullable=True)                # GNF per unit, for perte d'exploitation
    icon                        = db.Column(db.String(30),  nullable=True)
    sort_order                  = db.Column(db.Integer,     nullable=False, default=0)
    is_active                   = db.Column(db.Boolean,     nullable=False, default=True)  # soft delete = archive


class Vehicle(db.Model):
    __tablename__ = "vehicles"
    # Code is unique only among LIVE rows (not soft-deleted), so the code of a
    # deleted machine can be reused. A partial unique index enforces this at the
    # DB level on both SQLite and Postgres.
    __table_args__ = (
        db.Index("uq_vehicles_code_live", "code", unique=True,
                 sqlite_where=db.text("deleted_at IS NULL"),
                 postgresql_where=db.text("deleted_at IS NULL")),
    )

    id                            = db.Column(db.Integer, primary_key=True)
    code                          = db.Column(db.String(30), nullable=False)
    category_id                   = db.Column(db.Integer,   db.ForeignKey("vehicle_categories.id"), nullable=False)
    fleet_id                      = db.Column(db.Integer,   db.ForeignKey("fleets.id"),             nullable=False)
    description                   = db.Column(db.String(200), nullable=True)
    site                          = db.Column(db.String(80),  nullable=True)
    baseline_l_per_unit_override  = db.Column(db.Float,     nullable=True)
    cost_per_unit_override        = db.Column(db.Integer,   nullable=True)
    # Optional default driver — a real link to a registered Operator (same
    # fleet), used to pre-fill the conducteur on a new daily entry.
    default_operator_id           = db.Column(db.Integer,   db.ForeignKey("operators.id"), nullable=True)
    is_active                     = db.Column(db.Boolean,   nullable=False, default=True)
    # Soft delete beyond archive: set = the machine is gone from every UI, its
    # code is freed for reuse, but the row stays so history isn't orphaned.
    deleted_at                    = db.Column(db.DateTime,  nullable=True)
    photo_key                     = db.Column(db.String(200), nullable=True)   # S3 object key of the vehicle photo
    created_at                    = db.Column(db.DateTime,  nullable=False, default=datetime.utcnow)
    created_by                    = db.Column(db.Integer,   db.ForeignKey("users.id"), nullable=True)

    category         = db.relationship("VehicleCategory")
    fleet            = db.relationship("Fleet")
    default_operator = db.relationship("Operator", foreign_keys=[default_operator_id])

    @property
    def effective_baseline(self):
        return self.baseline_l_per_unit_override or (self.category and self.category.default_baseline_l_per_unit)

    @property
    def effective_cost_per_unit(self):
        return self.cost_per_unit_override or (self.category and self.category.default_cost_per_unit)


class Operator(db.Model):
    """Driver / operator pool for a fleet.

    DailyEntry.operator stays a String so historical entries are preserved
    if an operator is later renamed or deactivated; the Operator table is
    primarily the source for typeahead dropdowns and the operators admin
    page (similar to batmex_web's Operator model).
    """
    __tablename__ = "operators"
    __table_args__ = (
        db.UniqueConstraint("fleet_id", "name", name="uq_operators_fleet_name"),
    )

    id              = db.Column(db.Integer, primary_key=True)
    fleet_id        = db.Column(db.Integer, db.ForeignKey("fleets.id"), nullable=False)
    name            = db.Column(db.String(120), nullable=False)
    phone           = db.Column(db.String(30),  nullable=True)
    license_number  = db.Column(db.String(40),  nullable=True)
    notes           = db.Column(db.String(255), nullable=True)
    is_active       = db.Column(db.Boolean,     nullable=False, default=True)
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    created_by      = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)

    fleet = db.relationship("Fleet")


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
    # Fuel is NOT logged here — it is captured as an Expense (category 'fuel').
    trips            = db.Column(db.Integer, nullable=True)                  # for trip-tracked vehicles
    hours            = db.Column(db.Float,   nullable=True)                  # worked hours (direct, or index_end - index_start)
    # Hour-meter index readings for "hours_index" categories; hours = end - start.
    index_start      = db.Column(db.Float,   nullable=True)
    index_end        = db.Column(db.Float,   nullable=True)
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
    service_type    = db.Column(db.String(40),  nullable=True)               # which service this schedules: oil_change | filter | … (RECORD_TYPES)
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
    operator      = db.Column(db.String(120), nullable=True)   # driver linked to the service (name, like DailyEntry.operator)
    supplier      = db.Column(db.String(120), nullable=True)
    description   = db.Column(db.String(255), nullable=True)
    photo_key     = db.Column(db.String(200), nullable=True)
    recorded_by   = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    recorded_at   = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle")
    rule    = db.relationship("MaintenanceRule")
    # The service's cost lives in the money ledger, not here — one row per
    # record, created/updated/removed alongside it by the maintenance blueprint.
    expense = db.relationship(
        "Expense", uselist=False,
        primaryjoin="Expense.maintenance_record_id == MaintenanceRecord.id",
        foreign_keys="Expense.maintenance_record_id",
        cascade="all, delete-orphan",
    )
    # Parts taken from the store for this service. Deleting the record deletes
    # these movements, which puts the parts back in stock on its own.
    part_movements = db.relationship(
        "StockMovement", back_populates="record",
        foreign_keys="StockMovement.maintenance_record_id",
        cascade="all, delete-orphan",
    )

    @property
    def cost(self):
        """Read-through to the linked expense, so templates keep using `.cost`.
        Labour and outside work only — parts are valued from the store."""
        return self.expense.amount if self.expense else None

    @property
    def parts_value(self):
        """What the parts taken for this service were worth, at the value frozen
        when they left the store. Returns are netted off. Not money in the
        ledger: the parts were paid for when they were bought."""
        return sum(m.value or 0 for m in self.part_movements
                   if m.kind == "sortie") - \
               sum(m.value or 0 for m in self.part_movements
                   if m.kind == "retour")

    @property
    def total_cost(self):
        """Labour (ledger) + parts (store) — what the service really cost."""
        return (self.cost or 0) + self.parts_value


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

    vehicle = db.relationship("Vehicle")
    rule    = db.relationship("MaintenanceRule")


# ── Expenses ──────────────────────────────────────────────────────────────────


class Expense(db.Model):
    """THE single money ledger — every cost lands here, whatever the screen it
    was entered from:

      • Carburant page   → category "fuel"      (always tied to a vehicle)
      • Dépenses page    → accident / lavage / autre, tied to a vehicle or not
      • Fiche d'entretien → category "entretien", created automatically and
        linked back through maintenance_record_id (a service's cost lives here,
        never on MaintenanceRecord, so nothing is ever counted twice)
      • Entrée en stock  → category "pieces", linked through stock_movement_id.
        Buying parts is the only moment parts cost money: issuing one to a
        vehicle later adds NO row here, it only attributes this one.

    Scope rules: fleet_id set = a client's cost, visible to that fleet's users;
    fleet_id null = a company cost, visible to anyone who may see expenses.
    A cost with no vehicle must carry a `label` naming it.
    """
    __tablename__ = "expenses"

    id                    = db.Column(db.Integer, primary_key=True)
    vehicle_id            = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True)  # null = not tied to a vehicle
    fleet_id              = db.Column(db.Integer, db.ForeignKey("fleets.id"),   nullable=True)  # null = company-wide cost (no client)
    label                 = db.Column(db.String(120), nullable=True)                              # name of the cost — required when there is no vehicle
    category              = db.Column(db.String(40), nullable=False)                              # fuel | accident | lavage | autre | entretien (system)
    date                  = db.Column(db.String(10), nullable=False)
    amount                = db.Column(db.Integer,    nullable=False)                              # GNF
    liters                = db.Column(db.Float,      nullable=True)                               # for fuel category
    currency              = db.Column(db.String(5),  nullable=False, default="GNF")
    payment_method        = db.Column(db.String(20),  nullable=True)                              # mobile_money | cash | transfer | cheque | other
    payment_reference     = db.Column(db.String(60),  nullable=True)                              # cheque no., transfer ref., transaction id…
    operator              = db.Column(db.String(120), nullable=True)                              # optional driver this cost is attributed to (name, like DailyEntry.operator)
    supplier              = db.Column(db.String(120), nullable=True)
    description           = db.Column(db.String(255), nullable=True)
    photo_key             = db.Column(db.String(200), nullable=True)                              # S3 receipt

    # Cross-link when an expense IS a maintenance event (auto-closes the alert)
    maintenance_record_id = db.Column(db.Integer, db.ForeignKey("maintenance_records.id"), nullable=True)
    # … or when it IS a stock entry (parts bought for the store)
    stock_movement_id     = db.Column(db.Integer, db.ForeignKey("stock_movements.id"), nullable=True)

    created_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle")
    fleet   = db.relationship("Fleet")


# ── Caisse (petty cash) ───────────────────────────────────────────────────────


class CashMovement(db.Model):
    """Money put into the cash box.

    The other side — money going out — is already in the ledger: the costs
    entered on the Dépenses page. So this table holds only what comes in, and
    the balance is deposits minus those costs. One cash box for the company,
    like the parts store.

    `kind` leaves room for a withdrawal or a correction later; only "depot"
    exists today.
    """
    __tablename__ = "cash_movements"

    id         = db.Column(db.Integer,     primary_key=True)
    kind       = db.Column(db.String(20),  nullable=False, default="depot")
    date       = db.Column(db.String(10),  nullable=False)              # YYYY-MM-DD
    amount     = db.Column(db.Integer,     nullable=False)              # GNF
    currency   = db.Column(db.String(5),   nullable=False, default="GNF")
    source     = db.Column(db.String(120), nullable=True)   # who handed the money over
    method     = db.Column(db.String(20),  nullable=True)   # cash | mobile_money
    reference  = db.Column(db.String(60),  nullable=True)
    note       = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


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


# ── Fuel — citernes & movements ───────────────────────────────────────────────


class Citerne(db.Model):
    """A ZONE fuel tanker (citerne à carburant): a mobile reservoir that fills
    up at the client, parks, and dispenses fuel to the engins. Its live stock
    is computed from its movements (never stored), so there is one source of
    truth. Soft-deleted via is_active; only active citernes appear in the
    daily fuel saisie.
    """
    __tablename__ = "citernes"
    __table_args__ = (db.UniqueConstraint("code", name="uq_citernes_code"),)

    id              = db.Column(db.Integer,     primary_key=True)
    code            = db.Column(db.String(30),  nullable=False)               # e.g. CIT-GO
    name            = db.Column(db.String(120), nullable=False)
    capacity_liters = db.Column(db.Integer,     nullable=False)
    fleet_id        = db.Column(db.Integer,     db.ForeignKey("fleets.id"), nullable=False)
    is_active       = db.Column(db.Boolean,     nullable=False, default=True)  # soft delete = archive
    photo_key       = db.Column(db.String(200), nullable=True)   # S3 object key of the citerne photo
    created_at      = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    created_by      = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)

    fleet     = db.relationship("Fleet")
    movements = db.relationship("FuelMovement", back_populates="citerne",
                                foreign_keys="FuelMovement.citerne_id",
                                cascade="all, delete-orphan")

    @property
    def stock(self):
        """Litres currently in the tank = opening + rentrées − distributions,
        computed from the movement log. Relevés and conso do NOT move the
        reservoir stock (a relevé is a measurement; conso is fuel the citerne
        took at the client for its own engine, never from its reservoir)."""
        return self.stock_as_of(None)

    def stock_as_of(self, date_str):
        """Reservoir stock counting only movements dated on/before `date_str`
        (None = all). Used both for the live stock and for a relevé's écart."""
        total = 0
        for m in self.movements:
            # The opening balance is not a dated event: it is what the tank
            # held before any of this was logged, so it counts on every date,
            # including one earlier than the day the citerne was created.
            if (date_str is not None and m.date > date_str
                    and m.kind != "initial"):
                continue
            if m.kind in ("initial", "rentree"):
                total += m.liters
            elif m.kind == "distribution":
                total -= m.liters
        return total

    def _reservoir_movements(self):
        """Movements that move the reservoir, oldest first. Relevés and conso
        are left out: a relevé measures, and a conso is fuel taken at the client
        for the truck's own engine, never out of the tank."""
        return sorted((m for m in self.movements
                       if m.kind in ("initial", "rentree", "distribution")),
                      # The opening balance comes first whatever its date: it is
                      # the level the tank started at, not something that happened.
                      key=lambda m: (m.kind != "initial", m.date, m.id or 0))

    def _levels(self, date_str):
        """(level just before `date_str`, lowest after, highest after).

        Every check on a citerne needs these, never today's level: a movement
        carries a date, and it only shifts the levels from that date onward —
        the one before it stays put. Comparing against `stock`, an undated
        total, is what let a back-dated draw empty a tank that was full months
        later, and a back-dated fill overflow one since emptied.

        `before` is 0 when nothing came earlier, and None when no date is given
        (over the whole history there is no such starting point). Lowest and
        highest are None when nothing is dated at or after `date_str`.
        """
        running = 0
        before = 0 if date_str is not None else None
        lowest = highest = None
        for m in self._reservoir_movements():
            delta = m.liters if m.kind in ("initial", "rentree") else -m.liters
            if m.kind == "initial" or (date_str is not None and m.date < date_str):
                running += delta
                before = running
                continue
            running += delta
            lowest = running if lowest is None else min(lowest, running)
            highest = running if highest is None else max(highest, running)
        return before, lowest, highest

    @staticmethod
    def _pick(pick, *values):
        vals = [v for v in values if v is not None]
        return pick(vals) if vals else 0

    def min_stock_from(self, date_str=None):
        """The lowest the tank gets from `date_str` onward. What a draw has to
        fit inside — a draw lowers that date's level and every one after it."""
        before, lowest, _ = self._levels(date_str)
        return self._pick(min, before, lowest)

    def max_stock_from(self, date_str=None):
        """The highest the tank gets from `date_str` onward. What a fill has to
        leave room for under the capacity."""
        before, _, highest = self._levels(date_str)
        return self._pick(max, before, highest)

    def level_history(self):
        """[(movement, level after it)] oldest first, opening balance included.

        Cached on the instance: the movement table asks each row for its level,
        and recomputing the whole run per row would be quadratic.
        """
        cached = getattr(self, "_level_history", None)
        if cached is None:
            cached = []
            running = 0
            for m in self._reservoir_movements():
                running += m.liters if m.kind in ("initial", "rentree") else -m.liters
                cached.append((m, running))
            self._level_history = cached
        return cached

    @property
    def anomaly(self):
        """How far out of its bounds this citerne has ever been, or None.

        A tank holds between nothing and its capacity. Entry does not enforce
        that — a fill logged late, a draw entered before its rentrée, and the
        figures cross the line for a while. What matters is that the crossing
        is visible with its date, so it can be traced back and settled, rather
        than sitting in a total nobody questions.
        """
        low = high = None
        for m, level in self.level_history():
            if level < 0 and (low is None or level < low[1]):
                low = (m.date, level)
            if level > self.capacity_liters and (high is None or level > high[1]):
                high = (m.date, level)
        if not low and not high:
            return None
        return {
            "low": low[1] if low else None,
            "low_date": low[0] if low else None,
            "high": high[1] if high else None,
            "high_date": high[0] if high else None,
            # An anomaly the current figure already shows needs no second
            # badge — this one says the history is out of range even when
            # today's level looks fine.
            "past_only": self.stock >= 0 and self.stock <= self.capacity_liters,
        }

    def peak_if_returned(self, date_str, liters):
        """Highest level the tank would reach if `liters` went back in from this
        date on — deleting a distribution. The level before it does not move,
        so it is compared as it stands rather than raised too."""
        before, _, highest = self._levels(date_str)
        return self._pick(max, before,
                          None if highest is None else highest + liters)

    def floor_if_removed(self, date_str, liters):
        """Lowest level the tank would fall to if `liters` came out from this
        date on — deleting a rentrée. Same reasoning in reverse."""
        before, lowest, _ = self._levels(date_str)
        return self._pick(min, before,
                          None if lowest is None else lowest - liters)

    @property
    def opening_stock(self):
        """The opening balance (the 'initial' movement), what the citerne form
        edits — distinct from `stock`, which distributions have since reduced."""
        for m in self.movements:
            if m.kind == "initial":
                return m.liters
        return 0


class FuelMovement(db.Model):
    """One dated fuel movement of a citerne. `liters` is always positive; the
    sign is carried by `kind`:
        initial       — opening stock, set when the citerne is created (+)
        distribution  — an engin drew fuel from the citerne (−)
    (rentree / conso / releve arrive in a later step; the column already
    allows them so no migration is needed then.)
    """
    __tablename__ = "fuel_movements"

    id          = db.Column(db.Integer,     primary_key=True)
    # null for a "direct" fill — a bus takes fuel straight at the client, no citerne
    citerne_id  = db.Column(db.Integer,     db.ForeignKey("citernes.id"), nullable=True)
    kind        = db.Column(db.String(20),  nullable=False)                  # initial|rentree|distribution|releve|conso|direct
    date        = db.Column(db.String(10),  nullable=False)                  # YYYY-MM-DD
    time        = db.Column(db.String(5),   nullable=True)                   # HH:MM — when the fuel was actually taken (prises)
    liters      = db.Column(db.Integer,     nullable=False)                  # positive; sign from kind
    vehicle_id  = db.Column(db.Integer,     db.ForeignKey("vehicles.id"), nullable=True)  # the machine, for distributions
    operator    = db.Column(db.String(120), nullable=True)                  # driver who took the fuel
    note        = db.Column(db.String(255), nullable=True)
    created_by  = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    citerne = db.relationship("Citerne", back_populates="movements")
    vehicle = db.relationship("Vehicle")

    @property
    def level_after(self):
        """The citerne's level right after this movement, or None for the kinds
        that leave the reservoir alone (relevé, conso, direct)."""
        if not self.citerne or self.kind not in ("initial", "rentree", "distribution"):
            return None
        for m, level in self.citerne.level_history():
            if m.id == self.id:
                return level
        return None

    @property
    def out_of_range(self):
        """True when the tank stood outside [0, capacity] right after this
        movement — which is what makes the offending line findable."""
        level = self.level_after
        if level is None:
            return False
        return level < 0 or level > self.citerne.capacity_liters

    @property
    def ecart(self):
        """For a relevé: theoretical stock as of its date minus the physical
        reading. Positive = fuel missing (leak/theft/unlogged draw); negative =
        surplus. None for non-relevé movements."""
        if self.kind != "releve" or not self.citerne:
            return None
        return self.citerne.stock_as_of(self.date) - self.liters


# ── Parts store ───────────────────────────────────────────────────────────────


class Part(db.Model):
    """A maintenance part held in the workshop store (one store for the whole
    company, not per fleet).

    Like a Citerne, it carries no quantity of its own: what is on hand is
    computed from its movements, so the figure on screen and the history can
    never disagree. Soft-deleted via is_active.
    """
    __tablename__ = "parts"

    id            = db.Column(db.Integer,     primary_key=True)
    name          = db.Column(db.String(120), nullable=False)                  # e.g. Filtre à huile Perkins
    unit          = db.Column(db.String(20),  nullable=False, default="piece")  # piece | litre | kg | set
    reorder_level = db.Column(db.Float,       nullable=True)   # below this, the list flags it to re-order
    photo_key     = db.Column(db.String(200), nullable=True)
    is_active     = db.Column(db.Boolean,     nullable=False, default=True)     # soft delete = archive
    created_at    = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)
    created_by    = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)

    movements = db.relationship("StockMovement", back_populates="part",
                                foreign_keys="StockMovement.part_id",
                                cascade="all, delete-orphan")

    def _ordered_movements(self):
        return sorted(self.movements, key=lambda m: (m.date, m.id or 0))

    @property
    def quantity(self):
        """What is on hand right now."""
        return self.quantity_as_of(None)

    def quantity_as_of(self, date_str):
        """Quantity counting only movements dated on/before `date_str`
        (None = all). A physical count is a reset point: whatever was counted
        replaces the running figure, and later movements build on it. That is
        how a wrong figure — including a negative one — gets put right.
        """
        total = 0.0
        for m in self._ordered_movements():
            if date_str is not None and m.date > date_str:
                continue
            if m.kind == "inventaire":
                total = m.quantity
            elif m.kind == "sortie":
                total -= m.quantity
            else:                      # initial | entree | retour
                total += m.quantity
        return total

    @property
    def opening_quantity(self):
        """The opening balance (the 'initial' movement), what the part form
        edits — distinct from `quantity`, which later movements have moved."""
        for m in self.movements:
            if m.kind == "initial":
                return m.quantity
        return 0

    def average_cost_as_of(self, date_str=None):
        """Weighted average cost of one unit, over everything that came in on or
        before `date_str`. This is what a sortie is valued at — computed once,
        then frozen on the movement, so a later purchase can never change what
        a past service cost.

        Every price in here belongs to real parts: what the opening stock was
        declared to be worth, and what each receipt actually cost. There is no
        catalogue price to drift out of date. Falls back to the last price paid
        when nothing is left to average, and to None when the part has never
        been priced at all.
        """
        qty = value = 0.0
        last_price = None
        for m in self._ordered_movements():
            if date_str is not None and m.date > date_str:
                continue
            if m.kind in ("initial", "entree") and m.unit_price is not None:
                qty += m.quantity
                value += m.unit_price * m.quantity
                last_price = m.unit_price
        if qty > 0:
            return int(round(value / qty))
        return last_price

    @property
    def last_purchase_price(self):
        """The most recent price paid for this part — what pre-fills the next
        receipt. Receipts win over the opening stock whatever the dates say: the
        opening row is a declaration carrying the day it was typed, so it would
        otherwise mask every receipt logged for an earlier date. Falls back to
        the opening price, then to None for a part nobody has priced.
        """
        price = None
        for m in self._ordered_movements():
            if m.kind == "entree" and m.unit_price is not None:
                price = m.unit_price
        if price is not None:
            return price
        opening = next((m for m in self.movements if m.kind == "initial"), None)
        return opening.unit_price if opening else None

    @property
    def stock_value(self):
        """What the shelf is worth: quantity on hand × weighted average cost."""
        avg = self.average_cost_as_of()
        return int(round(self.quantity * avg)) if avg is not None else None

    @property
    def below_reorder(self):
        """True when it is time to re-order. No threshold set = never flagged."""
        return self.reorder_level is not None and self.quantity <= self.reorder_level


class StockMovement(db.Model):
    """One dated movement of a part. `quantity` is always positive; the sign is
    carried by `kind`:
        initial     — opening stock, set when the part is created (+)
        entree      — parts received (+); the only kind that costs money, so the
                      only one mirrored into the ledger as an Expense
        sortie      — parts issued for a service (−), linked to its record
        retour      — parts brought back unused (+)
        inventaire  — a physical count; resets the running quantity to what was
                      counted (see Part.quantity_as_of)

    Two price columns, and they mean different things: `unit_price` is what was
    actually paid on an entrée, `unit_value` is the weighted average frozen on a
    sortie. Keeping both is what lets the valuation method change later without
    rewriting a single past figure.
    """
    __tablename__ = "stock_movements"

    id          = db.Column(db.Integer,     primary_key=True)
    part_id     = db.Column(db.Integer,     db.ForeignKey("parts.id"), nullable=False)
    kind        = db.Column(db.String(20),  nullable=False)   # initial|entree|sortie|retour|inventaire
    date        = db.Column(db.String(10),  nullable=False)   # YYYY-MM-DD
    quantity    = db.Column(db.Float,       nullable=False)   # positive; sign from kind
    unit_price  = db.Column(db.Integer,     nullable=True)    # GNF paid — entrée / initial
    unit_value  = db.Column(db.Integer,     nullable=True)    # GNF frozen average — sortie / retour
    supplier    = db.Column(db.String(120), nullable=True)
    note        = db.Column(db.String(255), nullable=True)
    # Set on a sortie (or a retour): the service the parts went to
    maintenance_record_id = db.Column(db.Integer, db.ForeignKey("maintenance_records.id"), nullable=True)
    created_by  = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    part   = db.relationship("Part", back_populates="movements",
                             foreign_keys=[part_id])
    record = db.relationship("MaintenanceRecord", back_populates="part_movements",
                             foreign_keys=[maintenance_record_id])
    # An entrée's cost lives in the money ledger, not here — same arrangement as
    # a service and its expense, so the two can never disagree.
    expense = db.relationship(
        "Expense", uselist=False,
        primaryjoin="Expense.stock_movement_id == StockMovement.id",
        foreign_keys="Expense.stock_movement_id",
        cascade="all, delete-orphan",
    )

    @property
    def value(self):
        """What this movement is worth in GNF, or None when no price is known."""
        price = self.unit_price if self.unit_price is not None else self.unit_value
        return int(round(price * self.quantity)) if price is not None else None

    @property
    def ecart(self):
        """For a physical count: what was on the shelf before it, minus what was
        counted. Positive = the count came up short. None for other kinds."""
        if self.kind != "inventaire" or not self.part:
            return None
        expected = 0.0
        for m in self.part._ordered_movements():
            if (m.date, m.id or 0) >= (self.date, self.id or 0):
                continue
            if m.kind == "inventaire":
                expected = m.quantity
            elif m.kind == "sortie":
                expected -= m.quantity
            else:
                expected += m.quantity
        return expected - self.quantity


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
