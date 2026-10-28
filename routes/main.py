"""Protected staff dashboard, retired URLs and an independent health check."""

from flask import (
    Blueprint, abort, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)
import psycopg

from services.auth import ADMIN_ROLE, admin_required, check_csrf, csrf_token
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import AssessmentQueryError, get_active_dashboard_summary
from services.dashboard_patterns import select_area
from services.review_queue import get_dashboard_reviews
from services.staff_account_management import StaffAccountInputError, create_staff_account

main = Blueprint("main", __name__)


def require_fixture_source():
    """Keep the development-only source boundary for protected pages."""
    if current_app.extensions["development_workspace"].get("source_kind") != "development_fixture":
        abort(403)


def dashboard_summary(barangay=None):
    try:
        return get_active_dashboard_summary(barangay=barangay)
    except AssessmentQueryError:
        return None
    except InvalidAssessmentFilters:
        abort(400, description="Choose a valid barangay.")


def dashboard_reviews():
    """Personal review workload; a failure never hides the statistics."""
    try:
        return get_dashboard_reviews(g.user["id"])
    except (AssessmentQueryError, InvalidAssessmentFilters):
        return None


def render_dashboard(role_label, *, with_reviews):
    require_fixture_source()
    supplied = request.args.getlist("barangay")
    if len(supplied) > 1:
        abort(400, description="Choose a valid barangay.")
    return render_template(
        "staff/dashboard.html", summary=dashboard_summary(supplied[0] if supplied else None),
        selected_area=select_area(request.args.get("area")),
        show_reviews=with_reviews, reviews=dashboard_reviews() if with_reviews else None,
        welcome_name=(g.user.get("display_name") or g.user["username"]),
        role_label=role_label, staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@main.get("/")
def index():
    if g.user["role"] == ADMIN_ROLE:
        return redirect(url_for("main.administration"))
    return render_dashboard("LGU Staff", with_reviews=True)


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
    return render_dashboard("System Administrator", with_reviews=False)


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
