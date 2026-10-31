"""Protected staff dashboard, retired URLs and an independent health check."""

from flask import (
    Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
    request, url_for,
)
import psycopg

from services.auth import ADMIN_ROLE, admin_required, check_csrf, csrf_token
from services.assessment_queries import AssessmentQueryError, get_active_dashboard_summary
from services.staff_account_management import (
    StaffAccountInputError, create_staff_account, delete_staff_account,
    get_deletable_staff_account, get_staff_accounts, reveal_temporary_password,
)

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
@main.route("/administration/accounts/create", methods=["GET", "POST"],
            endpoint="staff_account_create")
@admin_required
def staff_accounts():
    require_fixture_source()
    show_create = request.endpoint == "main.staff_account_create"
    error = None
    created_account = None
    revealed_account = None
    display_name = ""
    username = ""
    if request.method == "POST":
        check_csrf()
        display_name = request.form.get("display_name", "")
        username = request.form.get("username", "")
        try:
            action = request.form.get("action", "create" if show_create else "reveal")
            if show_create and action == "create":
                created_account = create_staff_account(display_name, username)
            elif not show_create and action == "reveal":
                revealed_account = reveal_temporary_password(
                    request.form.get("account_id", type=int),
                )
            else:
                raise StaffAccountInputError("Choose a valid account action.")
        except StaffAccountInputError as exc:
            error = str(exc)
        except psycopg.errors.UniqueViolation:
            error = "That username is already in use. Choose another username."
        except (psycopg.Error, RuntimeError):
            abort(503, description="This account action is unavailable. Please contact the system administrator.")
    try:
        accounts = [] if show_create else get_staff_accounts()
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff accounts are unavailable. Please try again.")
    return render_template(
        "staff/staff_accounts.html", error=error, created_account=created_account,
        accounts=accounts, revealed_account=revealed_account, show_create=show_create,
        display_name=display_name, username=username,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), (400 if error else 200)


@main.route("/administration/accounts/<int:account_id>/delete", methods=["GET", "POST"])
@admin_required
def staff_account_delete(account_id):
    require_fixture_source()
    if request.method == "POST":
        check_csrf()
        try:
            delete_staff_account(account_id)
        except StaffAccountInputError:
            abort(404, description="This staff account is no longer available.")
        except psycopg.Error:
            abort(503, description="This account action is unavailable. Please try again.")
        flash("Staff account deleted.")
        return redirect(url_for("main.staff_accounts"))

    try:
        account = get_deletable_staff_account(account_id)
    except StaffAccountInputError:
        abort(404, description="This staff account is no longer available.")
    except psycopg.Error:
        abort(503, description="Staff accounts are unavailable. Please try again.")
    return render_template(
        "staff/delete_staff_account.html", account=account,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@main.get("/households/<path:retired_path>")
@main.get("/assessments/<path:retired_path>")
def retired_staff_pages(retired_path=None):
    """Retire old bookmarks without looking up or echoing any household information."""
    abort(410, description="This staff page has been retired while the interface is rebuilt.")


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
