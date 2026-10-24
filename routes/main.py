"""Protected staff dashboard, retired URLs and an independent health check."""

from flask import (
    Blueprint, abort, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)
import psycopg

from services.auth import ADMIN_ROLE, admin_required, check_csrf, csrf_token
from services.assessment_queries import AssessmentQueryError, get_active_dashboard_summary
from services.staff_account_management import StaffAccountInputError, create_staff_account

main = Blueprint("main", __name__)


def require_fixture_source():
    """Keep the development-only source boundary for protected pages."""
    if current_app.extensions["development_workspace"].get("source_kind") != "development_fixture":
        abort(403)


def dashboard_summary():
    try:
        return get_active_dashboard_summary()
    except AssessmentQueryError:
        return None


@main.get("/")
def index():
    if g.user["role"] == ADMIN_ROLE:
        return redirect(url_for("main.administration"))
    require_fixture_source()
    return render_template(
        "staff/dashboard.html", summary=dashboard_summary(), role_label="LGU Staff",
        staff_username=g.user["username"], csrf_token=csrf_token(),
    )


def render_admin_page(title, description=None):
    require_fixture_source()
    return render_template(
        "staff/administration.html", page_title=title, description=description,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@main.get("/administration")
@admin_required
def administration():
    require_fixture_source()
    return render_template(
        "staff/dashboard.html", summary=dashboard_summary(),
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@main.get("/administration/import")
@admin_required
def data_import():
    return render_admin_page("Data Import", "Data import tools will appear here.")


@main.route("/administration/accounts", methods=["GET", "POST"])
@admin_required
def staff_accounts():
    require_fixture_source()
    error = None
    created_account = None
    display_name = ""
    username = ""
    if request.method == "POST":
        check_csrf()
        display_name = request.form.get("display_name", "")
        username = request.form.get("username", "")
        try:
            created_account = create_staff_account(display_name, username)
        except StaffAccountInputError as exc:
            error = str(exc)
        except psycopg.errors.UniqueViolation:
            error = "That username is already in use. Choose another username."
        except (psycopg.Error, RuntimeError):
            abort(503, description="Staff account creation is unavailable. Please try again.")
    return render_template(
        "staff/staff_accounts.html", error=error, created_account=created_account,
        display_name=display_name, username=username,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), (400 if error else 200)


@main.get("/households/<path:retired_path>")
@main.get("/assessments/<path:retired_path>")
def retired_staff_pages(retired_path=None):
    """Retire old bookmarks without looking up or echoing any household information."""
    abort(410, description="This staff page has been retired while the interface is rebuilt.")


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
