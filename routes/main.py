"""Protected staff dashboard, retired URLs and an independent health check."""

from flask import (
    Blueprint, abort, current_app, g, jsonify, redirect, render_template,
    request, url_for,
)
import psycopg
from werkzeug.exceptions import RequestEntityTooLarge

from services.auth import ADMIN_ROLE, admin_required, check_csrf, csrf_token
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import AssessmentQueryError, get_active_dashboard_summary
from services.dashboard_patterns import select_area
from services.review_queue import get_dashboard_reviews
from services.staff_account_management import StaffAccountInputError, create_staff_account
from services.import_validation import (
    ImportValidationUnavailable, record_oversized_attempt, validate_admin_upload,
)
from services.import_validation_presentation import present_report
from services.import_assessment_preparation import (
    AssessmentPreparationError, list_import_batches, prepare_import_batch,
)

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


@main.get("/administration")
@admin_required
def administration():
    return render_dashboard("System Administrator", with_reviews=False)


@main.route("/administration/import", methods=["GET", "POST"])
@admin_required
def data_import():
    """Validate four historical CSV sources without changing a dataset batch."""
    require_fixture_source()
    report = None
    error = None
    batch_id = None
    status = 200
    if request.method == "POST":
        request.max_content_length = current_app.config["IMPORT_MAX_CONTENT_LENGTH"]
        try:
            check_csrf()
            action = request.form.get("action", "check")
            if action not in ("check", "store"):
                abort(400, description="Choose a valid data import action.")
            result = validate_admin_upload(
                request.files, g.user["id"], store_inactive=action == "store",
                authorization_confirmed=request.form.get("authorized_source") == "yes",
            )
            report = result["report"]
            error = result["error"]
            batch_id = result.get("batch_id")
            if error or (report and not report["passed"]):
                status = 400
        except RequestEntityTooLarge:
            try:
                record_oversized_attempt(g.user["id"])
            except ImportValidationUnavailable:
                abort(503, description="Data validation is unavailable. Please try again.")
            error = "The four files exceed the 256 MiB upload limit."
            status = 413
        except ImportValidationUnavailable:
            abort(503, description="Data validation is unavailable. Please try again.")
    return render_template(
        "staff/data_import.html", report=present_report(report), error=error,
        batch_id=batch_id,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), status


@main.route("/administration/import/batches", methods=["GET", "POST"])
@admin_required
def import_batches():
    """Prepare a validated inactive import without changing the active batch."""
    require_fixture_source()
    error = None
    prepared = None
    status = 200
    if request.method == "POST":
        check_csrf()
        selected = request.form.get("batch_id", "")
        if (not selected.isdecimal() or len(selected) > 18
                or request.form.get("confirm_prepare") != "yes"):
            error = "Select an inactive batch and confirm preparation."
            status = 400
        else:
            try:
                prepared = prepare_import_batch(int(selected), g.user["id"])
            except AssessmentPreparationError as exc:
                if str(exc) == "preparation_in_progress":
                    error = "An assessment preparation is already in progress. Try again later."
                    status = 409
                elif str(exc) == "batch_not_preparable":
                    error = "This batch is no longer available for preparation. Reload the page."
                    status = 409
                else:
                    error = "Assessment preparation could not be completed. The current dataset is unchanged."
                    status = 503
    try:
        batches = list_import_batches()
    except AssessmentPreparationError:
        abort(503, description="Import batches are unavailable. Please try again.")
    return render_template(
        "staff/import_batches.html", batches=batches, error=error, prepared=prepared,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), status


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
