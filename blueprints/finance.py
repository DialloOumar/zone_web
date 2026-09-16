"""Finance workspace — the accounting side of the app, built little by little.

Like batmex's BATMEX SA or HR: its own sidebar behind a switcher at the top of
the drawer, reached only by whoever the switcher is shown to. Today that is
the super admins and nobody else; every address under /finance answers "page
not found" to anyone else, so an unfinished screen is never seen by a user.

One guard at the door, below, covers every page added here later. When the
workspace is ready for a comptable, that guard is where a permission replaces
the super-admin rule — the pages themselves will not change.

Nothing is stubbed: the sidebar lists what works and nothing else.
"""
from flask import Blueprint, abort, render_template
from flask_login import current_user

from app import can_enter_finance, login_manager

finance_bp = Blueprint("finance", __name__, url_prefix="/finance")


@finance_bp.before_request
def _guard():
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    if not can_enter_finance():
        abort(404)


@finance_bp.route("/")
def index():
    return render_template("finance/home.html")
