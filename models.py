"""SQLAlchemy models for zone_web.

Schema overview (25 tables, grouped):

  Auth & access     User, Fleet, FleetRate, UserFleet, Role, Permission,
                    RolePermission
  Domain            VehicleCategory, Vehicle, Operator
  Daily ops         DailyEntry
  Maintenance       MaintenanceRule, MaintenanceRecord, Alert
  Money             Expense, CashMovement, CashAccount, Site
  Fuel              Citerne, FuelMovement
  Parts store       Part, StockMovement
  Workflow          PendingChange, AuditLog
  Config            AppSetting
"""
from datetime import date, datetime

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
    phone          = db.Column(db.String(30),  nullable=True)            # optional; printed on the bills they issue
    # Their stamp: the word in the middle ("Logistique"), the phone on the
    # lower arc, and the strokes of their signature as a transparent PNG,
    # laid over the stamp on the sheets they sign.
    stamp_label    = db.Column(db.String(40),  nullable=True)
    stamp_phone    = db.Column(db.String(30),  nullable=True)
    signature_png  = db.Column(db.LargeBinary, nullable=True)
    signature_at   = db.Column(db.DateTime,    nullable=True)
    # Where the strokes sit on the stamp, as the person set them: a shift in
    # stamp units (the stamp is 300 wide) and a size in percent.
    sig_dx         = db.Column(db.Integer,     nullable=False, default=0)
    sig_dy         = db.Column(db.Integer,     nullable=False, default=0)
    sig_scale      = db.Column(db.Integer,     nullable=False, default=100)
    stamp_color    = db.Column(db.String(7),   nullable=True)    # ink of the stamp itself, #rrggbb; empty = the stamp's blue
    sig_color      = db.Column(db.String(7),   nullable=True)    # ink of the strokes; empty = same as the stamp
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
    # Who signs a purchase order: the two signatures on the company's paper
    # bon de commande. A holder of a role with the box gives that approval.
    approves_logistics = db.Column(db.Boolean, nullable=False, default=False)
    approves_finance   = db.Column(db.Boolean, nullable=False, default=False)
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
    # Optional: a drawing from static/images/vehicle-defaults/ (the file name),
    # for vehicles of this category that have no photo of their own.
    default_image               = db.Column(db.String(80),  nullable=True)
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
    # The supplier this machine is leased from, when it is not the company's
    # own. One lessor at a time, so the link lives here and not in a table of
    # pairs. Its daily entries are what that supplier's bill is checked against.
    supplier_id                   = db.Column(db.Integer,   db.ForeignKey("suppliers.id"), nullable=True)
    # The client it works for, when placed with one. Its rate lives in
    # ClientRate, dated; the machine itself only knows where it is now.
    client_id                     = db.Column(db.Integer,   db.ForeignKey("clients.id"), nullable=True)
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
    supplier         = db.relationship("Supplier", back_populates="machines")
    client           = db.relationship("Client", back_populates="machines")

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


# ── Personnel (the seed of an HR module) ─────────────────────────────────────


class StaffPosition(db.Model):
    """What someone does: mécanicien, magasinier, gardien.

    A short list kept by hand rather than free text on each person, for the
    reason sites are a list: "all the mechanics" has to be a filter, and typed
    by hand the same job ends up spelled three ways.
    """
    __tablename__ = "staff_positions"

    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(80),  nullable=False, unique=True)
    sort_order = db.Column(db.Integer,     nullable=False, default=0)
    is_active  = db.Column(db.Boolean,     nullable=False, default=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


class Staff(db.Model):
    """Someone on the company's payroll.

    Deliberately not the same table as Operator. An operator is a driver
    attached to one client's fleet, and that list feeds the roster and the
    daily entries; the storekeeper and the accountant have no business in a
    pointing dropdown. So a driver who is also on the payroll sits in both
    lists for now, and the two can be tied together the day the HR module
    proper is built.

    The company's, not a client's: like sites, accounts and suppliers, a
    person carries no fleet.

    Today it exists so a cost can name who it was for. Everything an HR module
    needs later — contracts, leave, pay — hangs off this row.
    """
    __tablename__ = "staff"
    __table_args__ = (
        # Two people may share a surname, and two may share a given name; the
        # pair is what has to be unique.
        db.UniqueConstraint("last_name", "first_name", name="uq_staff_last_first"),
    )

    id          = db.Column(db.Integer,     primary_key=True)
    # Kept apart rather than as one line of text: a list of people is sorted and
    # searched by surname, and neither is possible once the two are run together.
    last_name   = db.Column(db.String(80),  nullable=False)
    # Optional, because somebody known by a single name should not be blocked
    # from being recorded at all.
    first_name  = db.Column(db.String(80))
    position_id = db.Column(db.Integer,     db.ForeignKey("staff_positions.id"), nullable=True)
    phone       = db.Column(db.String(30))
    # The number the company knows them by, when it uses one.
    matricule   = db.Column(db.String(40))
    hired_on    = db.Column(db.String(10))            # YYYY-MM-DD
    note        = db.Column(db.String(255))
    # Someone who leaves is archived, never deleted: the costs recorded against
    # them have to keep naming them.
    is_active   = db.Column(db.Boolean,     nullable=False, default=True)

    created_by  = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    position = db.relationship("StaffPosition")

    @property
    def name(self):
        """Surname then given name, which is the order a list of people is read
        in here. Everything on screen asks for this rather than the two halves,
        so how a person is written stays decided in one place."""
        return " ".join(part for part in (self.last_name, self.first_name) if part)


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
    # How many of whatever was bought, when the voucher says so ("5 ampoules").
    # A note on the cost, nothing more: it moves no stock and joins to nothing.
    quantity              = db.Column(db.Float,      nullable=True)
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
    # Where the money was spent. Optional, and what tells a field cost from an
    # office one: with a site it is terrain, without it société.
    site_id               = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=True)
    # Who the money went out for: an advance, a mission, a phone bill. A real
    # link, unlike the `operator` name above it, so one person is one row and
    # not three spellings.
    staff_id              = db.Column(db.Integer, db.ForeignKey("staff.id"), nullable=True)
    # Which purse the money came out of. Empty is the ordinary case: the cash in
    # the box. Naming an account says the box never held this money -- someone
    # advanced it -- and the matching money-in is written alongside so the box's
    # balance ends where it started. See sync_account_movement().
    account_id            = db.Column(db.Integer, db.ForeignKey("cash_accounts.id"), nullable=True)

    created_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle")
    fleet   = db.relationship("Fleet")
    site    = db.relationship("Site")
    account = db.relationship("CashAccount")
    staff   = db.relationship("Staff")
    # The instalment this cost settles, when it was paid against a supplier's
    # bill. Like the money-in below it, it belongs to the cost: written,
    # corrected and deleted with it, never on its own.
    supplier_payment = db.relationship(
        "SupplierPayment", uselist=False,
        primaryjoin="SupplierPayment.expense_id == Expense.id",
        foreign_keys="SupplierPayment.expense_id",
        back_populates="expense",
        cascade="all, delete-orphan",
    )
    # The money-in that pairs with this cost when an account paid it. It belongs
    # to the cost: written, corrected and deleted with it, never on its own.
    cash_movement = db.relationship(
        "CashMovement", uselist=False,
        primaryjoin="CashMovement.expense_id == Expense.id",
        foreign_keys="CashMovement.expense_id",
        back_populates="expense",
        cascade="all, delete-orphan",
    )


class Site(db.Model):
    """A place the company spends money: Siguiri, Mandiana, the yard.

    A short list the cashier keeps herself, so "all of Siguiri's costs in March"
    is a filter rather than a search through free text where one person wrote
    Siguiri and the next wrote siguiri.
    """
    __tablename__ = "sites"

    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(80),  nullable=False, unique=True)
    sort_order = db.Column(db.Integer,     nullable=False, default=0)
    is_active  = db.Column(db.Boolean,     nullable=False, default=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


class CashAccount(db.Model):
    """Where the cash box's money comes from, and where it goes back to.

    The boss's own account, the company's, a mobile-money agent who fronts the
    money when the box runs dry. Money in names the account it came from, money
    out names the one it went to, so each carries a running balance: what the
    box has taken from it, less what it has given back.

    `is_repayable` says how that balance reads. An agent's advance and the
    boss's own money are owed back; what the company puts in is its own. Same
    arithmetic, and without the flag the two would sit in one list looking alike.
    """
    __tablename__ = "cash_accounts"

    id           = db.Column(db.Integer,     primary_key=True)
    name         = db.Column(db.String(80),  nullable=False, unique=True)
    # Free text, not a number: an account here may be a bank account, a mobile
    # money line, or nothing at all -- so it is never checked or added up.
    number       = db.Column(db.String(60))
    is_repayable = db.Column(db.Boolean,     nullable=False, default=False)
    sort_order   = db.Column(db.Integer,     nullable=False, default=0)
    is_active    = db.Column(db.Boolean,     nullable=False, default=True)
    created_at   = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)


# ── Caisse (petty cash) ───────────────────────────────────────────────────────


class CashMovement(db.Model):
    """Money moving between the cash box and an account.

        depot   — money paid in, from an account (+)
        retrait — money taken back out to one (−)

    A withdrawal is not a cost: the boss taking his own money back spends
    nothing on the company's behalf, and filing it as an expense would inflate
    every cost total by his personal spending. The costs themselves stay in the
    ledger, so the balance is deposits, less withdrawals, less those costs.
    """
    __tablename__ = "cash_movements"

    id         = db.Column(db.Integer,     primary_key=True)
    kind       = db.Column(db.String(20),  nullable=False, default="depot")  # depot | retrait
    date       = db.Column(db.String(10),  nullable=False)              # YYYY-MM-DD
    amount     = db.Column(db.Integer,     nullable=False)              # GNF
    currency   = db.Column(db.String(5),   nullable=False, default="GNF")
    account_id = db.Column(db.Integer,     db.ForeignKey("cash_accounts.id"), nullable=True)
    source     = db.Column(db.String(120), nullable=True)   # free-text note, kept from before
    method     = db.Column(db.String(20),  nullable=True)   # cash | mobile_money
    reference  = db.Column(db.String(60),  nullable=True)
    note       = db.Column(db.String(255), nullable=True)
    # Set when this money-in was written for a cost an account paid directly.
    # Such a line is not the cashier's to edit: it mirrors the cost.
    expense_id = db.Column(db.Integer,     db.ForeignKey("expenses.id"), nullable=True, unique=True)
    created_by = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    account = db.relationship("CashAccount")
    expense = db.relationship("Expense", back_populates="cash_movement",
                              foreign_keys=[expense_id])


# ── Factures fournisseurs (invoices the company owes) ─────────────────


class Supplier(db.Model):
    """Someone who sends the company a bill: a garage, a parts shop, a landlord.

    Kept as a short list rather than free text for the same reason sites are:
    so "everything we owe Garage Diallo" is a filter and not a search through
    six spellings of the same name.
    """
    __tablename__ = "suppliers"

    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(80),  nullable=False, unique=True)
    # permanent: the garages and lessors worked with all year. divers: the
    # supplier of a day. The code says which at a glance -- FP-003 or FD-017 --
    # and is issued by the app when the supplier is created, never typed. A
    # supplier that changes kind is issued a code in its new series; only the
    # current code is ever shown.
    kind       = db.Column(db.String(10),  nullable=False, default="divers")
    code       = db.Column(db.String(12),  nullable=False, unique=True)
    contact    = db.Column(db.String(80))                   # phone, or whoever answers
    email      = db.Column(db.String(120))                  # printed on a bon de commande
    address    = db.Column(db.String(200))
    note       = db.Column(db.String(255))
    # Leases machines to the company and bills their hours and trips -- the
    # ones the daily entries record. Stored, not derived from having machines:
    # a lessor with none attached yet is still a lessor.
    provides_machines = db.Column(db.Boolean, nullable=False, default=False)
    # Sells parts to the store: the ones a bon de commande can be addressed
    # to. Independent of the above, so a supplier can be one, the other or
    # both. On by default: a supplier written down from the store is a parts
    # supplier by definition, and the bills page starts its box ticked.
    provides_parts = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    sort_order = db.Column(db.Integer,     nullable=False, default=0)
    is_active  = db.Column(db.Boolean,     nullable=False, default=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    machines = db.relationship("Vehicle", back_populates="supplier",
                               order_by="Vehicle.code")

    @property
    def live_machines(self):
        """Its machines that still exist: a deleted one keeps the link for its
        history but is nobody's to count."""
        return [v for v in self.machines if v.deleted_at is None]


SUPPLIER_CODE_PREFIX = {"permanent": "FP", "divers": "FD"}


@db.event.listens_for(Supplier, "before_insert")
def _supplier_code_on_insert(mapper, connection, target):
    """A supplier written without a code -- by a seed, a script, a test --
    still gets one, in its kind's series, so the NOT NULL never bites and the
    numbering never has a gap. The form issues its own the same way; this is
    the net under every other path."""
    if target.code:
        return
    prefix = SUPPLIER_CODE_PREFIX.get(target.kind or "divers", "FD") + "-"
    rows = connection.execute(
        db.select(Supplier.__table__.c.code)
        .where(Supplier.__table__.c.code.like(prefix + "%"))).fetchall()
    highest = 0
    for (code,) in rows:
        try:
            highest = max(highest, int(code[len(prefix):]))
        except (TypeError, ValueError):
            pass
    target.code = "%s%03d" % (prefix, highest + 1)


class SupplierInvoice(db.Model):
    """A bill the company has received and owes — the other direction from the
    facturation screen, which bills clients.

    This is a register, not a till: nothing here touches the cash box or the
    expense ledger. The money leaving is logged as an expense like any other,
    and an invoice recorded here as well would be the same money counted twice.
    What it answers is "what do we owe, and to whom".

    A bill is settled in instalments: the cash box pays part of it in cash
    this week, accounting wires the rest next month. Each of those is a
    SupplierPayment of its own, with its own date and its own method, so the
    history reads as it happened instead of collapsing into one figure.

    Nothing about the payment is stored here that can be worked out: what is
    paid is the sum of those rows, and paid / part-paid / untouched follows
    from it, so no stored figure can drift away from the instalments.
    """
    __tablename__ = "supplier_invoices"

    id          = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"), nullable=False)
    number      = db.Column(db.String(60))                  # the supplier's own reference
    date        = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD, the invoice's date
    # The day the paper actually reached the company, which is rarely the day
    # the supplier wrote on it. It is the one that says whether a bill has been
    # sitting on someone's desk, so it is asked for separately.
    received_on = db.Column(db.String(10))
    due_date    = db.Column(db.String(10))                  # when it falls due, if it says
    amount      = db.Column(db.Integer,    nullable=False)  # GNF
    currency    = db.Column(db.String(5),  nullable=False, default="GNF")
    description = db.Column(db.String(255))
    photo_key   = db.Column(db.String(200))                 # S3 photo of the paper
    # The purchase order this bill settles, when it came from one: what was
    # ordered, received and billed can then be read side by side.
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"), nullable=True, index=True)

    created_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    supplier = db.relationship("Supplier")
    purchase_order = db.relationship("PurchaseOrder", backref=db.backref("invoices", order_by="SupplierInvoice.date"))
    payments = db.relationship(
        "SupplierPayment", back_populates="invoice",
        order_by="SupplierPayment.date",
        cascade="all, delete-orphan")

    @property
    def paid_amount(self):
        """The instalments added up, never counting past the bill itself: an
        overpayment keyed in by mistake must not make other bills read as
        owing less than they do."""
        return min(sum(p.amount or 0 for p in self.payments), self.amount or 0)

    @property
    def remaining(self):
        """What is still owed on it."""
        return max((self.amount or 0) - self.paid_amount, 0)

    @property
    def status(self):
        """paid | partial | unpaid — read off the instalments, never stored."""
        if self.remaining <= 0:
            return "paid"
        return "partial" if self.payments else "unpaid"

    def is_overdue(self, today=None):
        """Past its due date with money still on it. Dates are YYYY-MM-DD
        strings, so comparing them as text is comparing them as dates."""
        if not self.due_date or self.remaining <= 0:
            return False
        return self.due_date < (today or date.today().isoformat())


class SupplierPayment(db.Model):
    """One instalment against a supplier's bill.

    Where the money came from decides where it is entered, and the two never
    overlap:

      • the cash box paid it — then it is entered on the Dépenses page like any
        other cost, and the cost writes this row beside itself. It belongs to
        that cost: corrected and deleted with it, never on its own, so the
        money that left the box is described in exactly one place.

      • anyone else paid it — accounting wiring the balance, say. Those people
        have no business in the cash box and no access to it, so they enter the
        instalment on the invoice itself and `expense_id` stays empty. Nothing
        touches the box, because nothing left it.

    That split is what keeps a payment from being counted twice: a row with an
    expense is the cash box's and cannot be edited from the invoice; a row
    without one never reaches the cash box at all.
    """
    __tablename__ = "supplier_payments"

    id         = db.Column(db.Integer,     primary_key=True)
    invoice_id = db.Column(db.Integer,     db.ForeignKey("supplier_invoices.id"),
                           nullable=False)
    date       = db.Column(db.String(10),  nullable=False)   # YYYY-MM-DD
    amount     = db.Column(db.Integer,     nullable=False)   # GNF
    method     = db.Column(db.String(20))                    # cash | mobile_money | transfer | cheque
    reference  = db.Column(db.String(60))                    # cheque no., transfer ref…
    # Where the money came from, when it is known: the company's bank account,
    # the Orange Money line, the boss's own. A label -- it moves no balance.
    # On the cash box's instalments it mirrors the cost's "Payé par".
    account_id = db.Column(db.Integer,     db.ForeignKey("cash_accounts.id"), nullable=True)
    # Set when the cash box paid: the cost this instalment mirrors.
    expense_id = db.Column(db.Integer,     db.ForeignKey("expenses.id"),
                           nullable=True, unique=True)

    created_by = db.Column(db.Integer,  db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    invoice = db.relationship("SupplierInvoice", back_populates="payments")
    account = db.relationship("CashAccount")
    expense = db.relationship("Expense", back_populates="supplier_payment",
                              foreign_keys=[expense_id])

    @property
    def from_cash_box(self):
        """True when this instalment is the cash box's, and so is read-only on
        the invoice."""
        return self.expense_id is not None


# ── Clients (who the machines work for) ─────────────────────────────────────


class Client(db.Model):
    """Who the machines work for. The mirror of a lessor supplier: machines
    are placed with a client and each is priced there, in ClientRate. The
    address, NIF and RCCM are for the day a bill has to carry them.

    Codes are CL-001, CL-002... issued by the app when the client is created,
    never typed. A client that was ever priced is archived, not deleted.
    """
    __tablename__ = "clients"

    id         = db.Column(db.Integer,     primary_key=True)
    name       = db.Column(db.String(80),  nullable=False, unique=True)
    code       = db.Column(db.String(12),  nullable=False, unique=True)
    contact    = db.Column(db.String(80))
    address    = db.Column(db.String(200))
    tax_id     = db.Column(db.String(40))                   # NIF
    rccm       = db.Column(db.String(40))
    note       = db.Column(db.String(255))
    sort_order = db.Column(db.Integer,     nullable=False, default=0)
    is_active  = db.Column(db.Boolean,     nullable=False, default=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    machines = db.relationship("Vehicle", back_populates="client", order_by="Vehicle.code")
    rates    = db.relationship("ClientRate", back_populates="client",
                               cascade="all, delete-orphan")

    @property
    def live_machines(self):
        """Its machines that still exist: a deleted one keeps the link for
        its history but is nobody's to count."""
        return [v for v in self.machines if v.deleted_at is None]

    @property
    def has_history(self):
        """True once anything was ever priced for it -- then it is archived,
        never deleted."""
        return bool(self.rates)


class ClientRate(db.Model):
    """Dated price of one machine for one client: GNF per worked unit (trip
    or hour, whichever its category counts). Append-only: a new price is a
    new row with its date of effect, so a past month re-bills at the price
    that applied then. In force on a date = greatest effective_from <= date.
    """
    __tablename__ = "client_rates"

    id             = db.Column(db.Integer,    primary_key=True)
    client_id      = db.Column(db.Integer,    db.ForeignKey("clients.id"),  nullable=False, index=True)
    vehicle_id     = db.Column(db.Integer,    db.ForeignKey("vehicles.id"), nullable=False, index=True)
    rate_per_unit  = db.Column(db.Integer,    nullable=False)              # GNF per trip / hour
    effective_from = db.Column(db.String(10), nullable=False)              # YYYY-MM-DD
    created_at     = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)
    created_by     = db.Column(db.Integer,    db.ForeignKey("users.id"), nullable=True)

    client  = db.relationship("Client", back_populates="rates")
    vehicle = db.relationship("Vehicle")


class ClientInvoice(db.Model):
    """A bill issued to a client for a month of its machines' work.

    Issuing freezes the month's math into lines: a later correction of a
    daily entry does not move a bill already sent. What a client owes is the
    total less what it has paid (ClientPayment, next); a cancelled bill is
    kept in the register, struck through, and frees its month.

    Numbers are FAC-2026-001, FAC-2026-002... per year of issue, given by the
    app when the bill is issued. Amounts are GNF, no decimals.
    """
    __tablename__ = "client_invoices"

    id         = db.Column(db.Integer,     primary_key=True)
    client_id  = db.Column(db.Integer,     db.ForeignKey("clients.id"), nullable=False, index=True)
    number     = db.Column(db.String(20),  nullable=False, unique=True)
    period     = db.Column(db.String(7),   nullable=False)               # YYYY-MM billed
    date       = db.Column(db.String(10),  nullable=False)               # issue date
    due_date   = db.Column(db.String(10),  nullable=True)
    total      = db.Column(db.Integer,     nullable=False)
    status     = db.Column(db.String(12),  nullable=False, default="issued")  # issued | cancelled
    # What the bill is for, in the client's words ("Location juillet 2026"),
    # and the reference the client gave, a purchase order for instance.
    subject    = db.Column(db.String(120), nullable=True)
    client_ref = db.Column(db.String(60),  nullable=True)
    # Whom the client should call about this bill: the person who issued it,
    # by default, copied in so the bill still says so after they leave.
    contact_name  = db.Column(db.String(120), nullable=True)
    contact_phone = db.Column(db.String(60),  nullable=True)
    note       = db.Column(db.String(255), nullable=True)
    created_by = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    client   = db.relationship("Client")
    issuer   = db.relationship("User", foreign_keys=[created_by])
    lines    = db.relationship("ClientInvoiceLine", back_populates="invoice",
                               cascade="all, delete-orphan", order_by="ClientInvoiceLine.id")
    payments = db.relationship("ClientPayment", back_populates="invoice",
                               cascade="all, delete-orphan", order_by="ClientPayment.date, ClientPayment.id")

    @property
    def is_cancelled(self):
        return self.status == "cancelled"

    @property
    def paid_amount(self):
        return sum(p.amount or 0 for p in self.payments)

    @property
    def remaining(self):
        return max((self.total or 0) - self.paid_amount, 0)

    @property
    def pay_status(self):
        """paid | partial | unpaid | cancelled -- read off the payments,
        never stored, so it can never disagree with them."""
        if self.is_cancelled:
            return "cancelled"
        if self.remaining <= 0:
            return "paid"
        return "partial" if self.payments else "unpaid"

    def is_overdue(self, today):
        """Past its due date with money still owed."""
        return bool(self.due_date) and not self.is_cancelled and self.remaining > 0 and self.due_date < today


class ClientPayment(db.Model):
    """One payment received against a client's bill: when, how much, how, and
    on which of the company's accounts it arrived. Part payments add up; the
    bill's state is read off them."""
    __tablename__ = "client_payments"

    id         = db.Column(db.Integer,     primary_key=True)
    invoice_id = db.Column(db.Integer,     db.ForeignKey("client_invoices.id"), nullable=False, index=True)
    date       = db.Column(db.String(10),  nullable=False)   # YYYY-MM-DD
    amount     = db.Column(db.Integer,     nullable=False)   # GNF
    method     = db.Column(db.String(20))                    # cash | mobile_money | transfer | cheque
    reference  = db.Column(db.String(60))                    # the client's transfer or cheque number
    account_id = db.Column(db.Integer,     db.ForeignKey("cash_accounts.id"), nullable=True)
    note       = db.Column(db.String(255))
    created_by = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    invoice = db.relationship("ClientInvoice", back_populates="payments")
    account = db.relationship("CashAccount")


class ClientInvoiceLine(db.Model):
    """One line of a client's bill: a machine, at one rate, for the units it
    worked at that rate. A machine whose price changed mid-month has two
    lines. The code and category are copied in, so the line still reads if
    the machine is later renamed or removed."""
    __tablename__ = "client_invoice_lines"

    id             = db.Column(db.Integer,    primary_key=True)
    invoice_id     = db.Column(db.Integer,    db.ForeignKey("client_invoices.id"), nullable=False, index=True)
    vehicle_id     = db.Column(db.Integer,    db.ForeignKey("vehicles.id"), nullable=True)
    vehicle_code   = db.Column(db.String(30), nullable=False)
    category_label = db.Column(db.String(80), nullable=False)
    unit_type      = db.Column(db.String(10), nullable=False)             # trips | hours
    units          = db.Column(db.Float,      nullable=False)
    rate           = db.Column(db.Integer,    nullable=False)             # GNF per unit
    amount         = db.Column(db.Integer,    nullable=False)

    invoice = db.relationship("ClientInvoice", back_populates="lines")
    vehicle = db.relationship("Vehicle")


@db.event.listens_for(Client, "before_insert")
def _client_code_on_insert(mapper, connection, target):
    """A client written without a code -- by a seed, a script, a test --
    still gets one, so the NOT NULL never bites and the numbering never has
    a gap. The form issues its own the same way; this is the net."""
    if target.code:
        return
    rows = connection.execute(
        db.select(Client.__table__.c.code)
        .where(Client.__table__.c.code.like("CL-%"))).fetchall()
    highest = 0
    for (code,) in rows:
        try:
            highest = max(highest, int(code[3:]))
        except (TypeError, ValueError):
            pass
    target.code = "CL-%03d" % (highest + 1)


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
        """Litres currently in the tank = rentrées − distributions, computed
        from the movement log. A citerne starts empty. Relevés and conso do NOT move the
        reservoir stock (a relevé is a measurement; conso is fuel the citerne
        took at the client for its own engine, never from its reservoir)."""
        return self.stock_as_of(None)

    def stock_as_of(self, date_str):
        """Reservoir stock counting only movements dated on/before `date_str`
        (None = all). Used both for the live stock and for a relevé's écart."""
        total = 0
        for m in self.movements:
            if date_str is not None and m.date > date_str:
                continue
            if m.kind == "rentree":
                total += m.liters
            elif m.kind == "distribution":
                total -= m.liters
        return total

    @staticmethod
    def instant(date_str, time_str=None):
        """Sort/compare key for a moment in the movement log: (date, time).
        A movement with no time counts as the start of its day, so a rentrée
        logged without an hour still serves any draw taken that day, while a
        draw without an hour is checked against the level at dawn — the same
        cautious reading the log had before times existed."""
        return (date_str, time_str or "00:00")

    def _reservoir_movements(self):
        """Movements that move the reservoir, oldest first — by date, then by
        time of day, then by entry order. Relevés and conso are left out: a
        relevé measures, and a conso is fuel taken at the client for the
        truck's own engine, never out of the tank."""
        return sorted((m for m in self.movements
                       if m.kind in ("rentree", "distribution")),
                      key=lambda m: self.instant(m.date, m.time) + (m.id or 0,))

    def _levels(self, date_str, time_str=None):
        """(level just before the moment `date_str` `time_str`, lowest from it
        on, highest from it on).

        Every check on a citerne needs these, never today's level: a movement
        carries a date and an hour, and it only shifts the levels from that
        moment onward — the one before it stays put. Comparing against `stock`,
        an undated total, is what let a back-dated draw empty a tank that was
        full months later, and a back-dated fill overflow one since emptied.

        The hour matters within a day: a rentrée at 08:00 is already in the
        tank for a draw at 14:00 the same day, so it belongs to `before`; a
        rentrée at 16:00 is not, and stays in the "after" run. Without a time
        the moment is the start of the day (see `instant`).

        `before` is 0 when nothing came earlier, and None when no date is given
        (over the whole history there is no such starting point). Lowest and
        highest are None when nothing is dated at or after the moment.
        """
        running = 0
        before = 0 if date_str is not None else None
        lowest = highest = None
        at = self.instant(date_str, time_str) if date_str is not None else None
        for m in self._reservoir_movements():
            delta = m.liters if m.kind == "rentree" else -m.liters
            if at is not None and self.instant(m.date, m.time) < at:
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

    def min_stock_from(self, date_str=None, time_str=None):
        """The lowest the tank gets from the moment `date_str` `time_str`
        onward. What a draw has to fit inside — a draw lowers the level from
        its own hour and every one after it, so a fill earlier that same day
        already counts."""
        before, lowest, _ = self._levels(date_str, time_str)
        return self._pick(min, before, lowest)

    def max_stock_from(self, date_str=None, time_str=None):
        """The highest the tank gets from the moment `date_str` `time_str`
        onward. What a fill has to leave room for under the capacity."""
        before, _, highest = self._levels(date_str, time_str)
        return self._pick(max, before, highest)

    def level_history(self):
        """[(movement, level after it)] oldest first.

        Cached on the instance: the movement table asks each row for its level,
        and recomputing the whole run per row would be quadratic.
        """
        cached = getattr(self, "_level_history", None)
        if cached is None:
            cached = []
            running = 0
            for m in self._reservoir_movements():
                running += m.liters if m.kind == "rentree" else -m.liters
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

    def peak_if_returned(self, date_str, liters, time_str=None):
        """Highest level the tank would reach if `liters` went back in from this
        date on — deleting a distribution. The level before it does not move,
        so it is compared as it stands rather than raised too."""
        before, _, highest = self._levels(date_str, time_str)
        return self._pick(max, before,
                          None if highest is None else highest + liters)

    def floor_if_removed(self, date_str, liters, time_str=None):
        """Lowest level the tank would fall to if `liters` came out from this
        date on — deleting a rentrée. Same reasoning in reverse."""
        before, lowest, _ = self._levels(date_str, time_str)
        return self._pick(min, before,
                          None if lowest is None else lowest - liters)



class FuelMovement(db.Model):
    """One dated fuel movement. `liters` is always positive; the sign is
    carried by `kind`:
        rentree       — the citerne filled up at the client (+)
        distribution  — an engin drew fuel from the citerne (−)
        releve        — a gauge reading; moves nothing
        conso         — fuel the citerne took for its own engine; moves nothing
        direct        — a machine filled straight at the client, no citerne
    A citerne starts empty; there is no opening-stock kind any more.
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
        if not self.citerne or self.kind not in ("rentree", "distribution"):
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


class PartUnit(db.Model):
    """A unit the store counts in -- pièce, litre, kg, jeu -- kept by the
    store itself. A part stores the unit's code; the name is what every
    screen prints. The four starters are seeded; the rest is theirs to add.
    A unit some part counts in is archived, never deleted."""
    __tablename__ = "part_units"

    id         = db.Column(db.Integer,    primary_key=True)
    code       = db.Column(db.String(20), nullable=False, unique=True)
    name       = db.Column(db.String(40), nullable=False, unique=True)
    sort_order = db.Column(db.Integer,    nullable=False, default=0)
    is_active  = db.Column(db.Boolean,    nullable=False, default=True)
    created_at = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)


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
    # What the supplier sells it in, when that is not the unit the store
    # counts: a "fût" of 200 litres. An order may then be written per pack,
    # and the receipt converts it back to litres.
    pack_name     = db.Column(db.String(40),  nullable=True)
    pack_size     = db.Column(db.Float,       nullable=True)   # how many units one pack holds
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
    # Set when this receipt came in against a purchase order line. Its money
    # side is the supplier's bill, not a cash-box expense.
    purchase_line_id = db.Column(db.Integer, db.ForeignKey("purchase_order_lines.id"), nullable=True)
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


# ── Finance — plan comptable ──────────────────────────────────────────────────


class LedgerAccount(db.Model):
    """One account of the plan comptable.

    The SYSCOHADA révisé chart -- the plan every company in Guinea keeps its
    books on -- is seeded from data/plan_comptable_syscohada.csv on every
    boot, so each installation starts with the same 1,300-odd accounts. The
    company adds its own under any of them: one per supplier under 4011, one
    per bank under 521. A standard account is never edited or deleted, only
    hidden; a company account can be renamed, and removed while unused.

    `parent_code` is the nearest account above it in the chart (245 for 2451,
    24 for 245), so a class can be shown as a tree without parsing codes.
    """
    __tablename__ = "ledger_accounts"

    id          = db.Column(db.Integer,     primary_key=True)
    code        = db.Column(db.String(12),  nullable=False, unique=True)
    label       = db.Column(db.String(200), nullable=False)
    klass       = db.Column(db.Integer,     nullable=False)   # 1..9, the code's first digit
    parent_code = db.Column(db.String(12),  nullable=True, index=True)
    is_standard = db.Column(db.Boolean,     nullable=False, default=False)  # from the SYSCOHADA file
    is_active   = db.Column(db.Boolean,     nullable=False, default=True)
    created_by  = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    @property
    def depth(self):
        return len(self.code)

    def __repr__(self):
        return f"<LedgerAccount {self.code} {self.label!r}>"


# ── Bons de commande ─────────────────────────────────────────────────────────


class PurchaseOrder(db.Model):
    """What the store orders from a supplier, and who signed it.

    Two signatures, as on the company's paper order: logistics, then
    finance, each a user and a time. The status follows them: pending
    logistics, pending finance, approved; or rejected, with who, when and
    why. Totals are stored when the lines are written, so the register
    never adds them up row by row.
    """
    __tablename__ = "purchase_orders"

    id             = db.Column(db.Integer,     primary_key=True)
    number         = db.Column(db.String(20),  nullable=False, unique=True)   # BC-2026-001
    supplier_id    = db.Column(db.Integer,     db.ForeignKey("suppliers.id"), nullable=False, index=True)
    date           = db.Column(db.String(10),  nullable=False)
    status         = db.Column(db.String(20),  nullable=False, default="pending_logistics")
    requested_by   = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    requester_name = db.Column(db.String(120), nullable=True)    # copied in, for the sheet
    requester_phone = db.Column(db.String(60), nullable=True)
    logistics_by   = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    logistics_at   = db.Column(db.DateTime,    nullable=True)
    finance_by     = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    finance_at     = db.Column(db.DateTime,    nullable=True)
    rejected_by    = db.Column(db.Integer,     db.ForeignKey("users.id"), nullable=True)
    rejected_at    = db.Column(db.DateTime,    nullable=True)
    rejected_reason = db.Column(db.String(255), nullable=True)
    rejections     = db.Column(db.Integer,     nullable=False, default=0)   # how many times it was sent back
    total_gross    = db.Column(db.Integer,     nullable=False, default=0)   # before discounts
    total_discount = db.Column(db.Integer,     nullable=False, default=0)
    total          = db.Column(db.Integer,     nullable=False, default=0)
    note           = db.Column(db.String(255), nullable=True)
    created_at     = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    supplier         = db.relationship("Supplier")
    requester        = db.relationship("User", foreign_keys=[requested_by])
    logistics_signer = db.relationship("User", foreign_keys=[logistics_by])
    finance_signer   = db.relationship("User", foreign_keys=[finance_by])
    rejecter         = db.relationship("User", foreign_keys=[rejected_by])
    lines = db.relationship("PurchaseOrderLine", back_populates="order",
                            cascade="all, delete-orphan", order_by="PurchaseOrderLine.id")

    @property
    def received_value(self):
        """What has come in, priced as the lines were: received units × the
        line's unit cost, plus a non-stock line once it is ticked off."""
        total = 0
        for l in self.lines:
            if l.part_id:
                total += int(round((l.received_qty or 0) * l.stock_unit_price))
            elif l.received_qty:
                total += l.amount
        return total

    @property
    def invoiced_total(self):
        return sum(i.amount or 0 for i in self.invoices)


class PurchaseOrderLine(db.Model):
    """One line: a part from the catalogue or a free description, the
    quantity, the unit price, the discount as an amount, and the line's
    amount after it."""
    __tablename__ = "purchase_order_lines"

    id          = db.Column(db.Integer,     primary_key=True)
    order_id    = db.Column(db.Integer,     db.ForeignKey("purchase_orders.id"), nullable=False, index=True)
    part_id     = db.Column(db.Integer,     db.ForeignKey("parts.id"), nullable=True)
    description = db.Column(db.String(160), nullable=False)
    reference   = db.Column(db.String(60),  nullable=True)
    quantity    = db.Column(db.Float,       nullable=False)   # in packs when in_pack, else in the part's unit
    in_pack     = db.Column(db.Boolean,     nullable=False, default=False)
    unit_price  = db.Column(db.Integer,     nullable=False)   # GNF, per pack or per unit likewise
    discount    = db.Column(db.Integer,     nullable=False, default=0)   # GNF, on the line
    amount      = db.Column(db.Integer,     nullable=False)   # quantity × price − discount
    received_qty = db.Column(db.Float,      nullable=False, default=0)   # in the part's unit
    # For a line that is a new article, not yet in the catalogue: the unit it
    # will be counted in once it is created at receipt.
    new_unit    = db.Column(db.String(20),  nullable=True)

    order = db.relationship("PurchaseOrder", back_populates="lines")
    part  = db.relationship("Part")
    receipts = db.relationship("StockMovement", backref="purchase_line")

    @property
    def stock_qty(self):
        """The line in the store's own unit: packs × contents when bought
        per pack, the quantity itself otherwise."""
        if self.in_pack and self.part and self.part.pack_size:
            return self.quantity * self.part.pack_size
        return self.quantity

    @property
    def remaining_qty(self):
        return max(self.stock_qty - (self.received_qty or 0), 0)

    @property
    def stock_unit_price(self):
        """What one unit of stock cost on this line, discount included."""
        return int(round(self.amount / self.stock_qty)) if self.stock_qty else self.unit_price


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
