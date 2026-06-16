"""Admin blueprint — super-admin-only configuration pages.

Currently implements Fleets. Users, roles, vehicle categories, settings, and
the audit log follow as further route groups in this same blueprint.

Helpers (super_admin_required, log_action, slugify, get_t) are imported from
app.py; this module is imported at the bottom of app.py once those exist, so
there is no circular-import problem.
"""
from flask import (Blueprint, abort, flash, redirect, render_template,
                   request, url_for)
from flask_login import login_required

from app import (get_t, is_modal_request, log_action, modal_ok, slugify,
                 super_admin_required)
from models import Fleet, Operator, UserFleet, Vehicle, VehicleCategory, db

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
