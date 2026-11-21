"""Protected staff dashboard, retired URLs and an independent health check."""

from flask import (
    Blueprint, Response, abort, current_app, flash, g, jsonify, redirect,
    render_template, request, url_for,
)
import psycopg
from werkzeug.exceptions import RequestEntityTooLarge

from services.auth import (
    ADMIN_ROLE, admin_required, check_csrf, csrf_token, has_permission,
    lgu_staff_required, start_session,
)
from services.admin_dashboard import (
    AdminDashboardSummaryError, get_import_dashboard_summary,
)
from services.admin_profile import (
    AdminProfileInputError, change_admin_password, change_admin_username,
)
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import (
    AssessmentQueryError, get_active_barangay_priority_summary,
    get_active_dashboard_summary,
)
from services.dashboard_patterns import select_area
from services.review_queue import get_admin_review_dashboard_summary, get_dashboard_reviews
from services.import_validation import (
    FILE_UPLOAD_BYTES, FILE_UPLOAD_MB, ImportPreviewError,
    ImportValidationUnavailable, create_import_preview_token,
    record_oversized_attempt, stored_survey_year_counts, validate_admin_upload,
    verify_import_preview_token,
)
from services.import_history import (
    ImportHistoryUnavailable, export_import_history, get_import_history,
)
from services.import_validation_presentation import present_import_result, present_report
from services.import_assessment_preparation import (
    AssessmentPreparationError, list_import_batches, prepare_import_batch,
)
from services.import_batch_deletion import BatchDeletionError, delete_import_batch
from services.survey_years import (
    MIN_SURVEY_YEAR, latest_survey_year, parse_survey_year,
    supported_survey_years,
)
from services.batch_activation import (
    BatchActivationError, active_dataset_exists, activate_prepared_batch,
    activation_overview,
    describe_citizen_work,
)
from services.staff_account_management import (
    StaffAccountHasActiveReviews, StaffAccountInputError, create_staff_account,
    deactivate_staff_account, get_staff_account_for_recovery,
    get_staff_account_dashboard_summary, get_staff_account_to_deactivate,
    get_staff_accounts, recover_staff_account, prepare_temporary_password_email,
    reveal_temporary_password,
)
from services.email_delivery import EmailDeliveryError, send_staff_temporary_password_email
from services.staff_profile import StaffProfileInputError, change_staff_password
from services.staff_password_reset_requests import (
    mark_reset_request_email_accepted, password_reset_request_overview,
    pending_password_reset_count,
)
from services.staff_access import (
    ACCESS_PRESETS, StaffAccessInputError, get_staff_access,
    get_staff_access_history, update_staff_access,
)

main = Blueprint("main", __name__)

ACCESS_PRESET_LABELS = {
    "dashboard_only": "Dashboard only",
    "household_viewer": "Household Records Viewer",
    "assessment_viewer": "Assessment Viewer",
    "case_reviewer": "Case Reviewer",
    "submissions_officer": "Submissions Officer",
    "select_all": "Select All",
}


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


def render_dashboard(role_label, *, with_reviews, initial_setup=False,
                     pending_reset_count=0, is_admin_dashboard=False,
                     staff_account_summary=None, admin_review_summary=None,
                     import_dashboard_summary=None, admin_barangay_summary=None):
    require_fixture_source()
    supplied = request.args.getlist("barangay")
    if len(supplied) > 1:
        abort(400, description="Choose a valid barangay.")
    summary = dashboard_summary(supplied[0] if supplied else None)
    can_view_households = has_permission("households.view")
    can_view_assessments = has_permission("assessments.view")
    can_view_reviews = has_permission("reviews.view")
    can_view_submissions = has_permission("submissions.view")
    if initial_setup:
        primary_url = url_for("main.data_import")
        primary_label = "Go to Data Import"
    elif can_view_assessments:
        primary_url = url_for(
            "assessments.prioritization",
            barangay=(summary.get("barangay") or None) if summary else None,
        )
        primary_label = "Explore households"
    elif can_view_households:
        primary_url = url_for("households.index")
        primary_label = "Open Household Records"
    else:
        primary_url = None
        primary_label = None
    return render_template(
        "staff/dashboard.html", summary=summary,
        selected_area=select_area(request.args.get("area")),
        show_reviews=with_reviews, reviews=dashboard_reviews() if with_reviews else None,
        welcome_name=(g.user.get("display_name") or g.user["username"]),
        initial_setup=initial_setup,
        pending_reset_count=pending_reset_count,
        is_admin_dashboard=is_admin_dashboard,
        staff_account_summary=staff_account_summary,
        admin_review_summary=admin_review_summary,
        import_dashboard_summary=import_dashboard_summary,
        admin_barangay_summary=admin_barangay_summary,
        role_label=role_label, staff_username=g.user["username"],
        can_view_households=can_view_households,
        can_view_assessments=can_view_assessments,
        can_view_reviews=can_view_reviews,
        can_view_submissions=can_view_submissions,
        dashboard_primary_url=primary_url, dashboard_primary_label=primary_label,
        csrf_token=csrf_token(),
    )


@main.get("/")
def index():
    if g.user["role"] == ADMIN_ROLE:
        return redirect(url_for("main.administration"))
    return render_dashboard("LGU Staff", with_reviews=has_permission("reviews.view"))


@main.get("/administration")
@admin_required
def administration():
    initial_setup = False
    if current_app.config.get("DATABASE_URL"):
        try:
            initial_setup = not active_dataset_exists()
        except BatchActivationError:
            pass
    try:
        reset_count = pending_password_reset_count()
    except (psycopg.Error, RuntimeError):
        reset_count = 0
    try:
        account_summary = get_staff_account_dashboard_summary()
    except (psycopg.Error, RuntimeError):
        account_summary = None
    try:
        review_summary = get_admin_review_dashboard_summary()
    except (AssessmentQueryError, InvalidAssessmentFilters):
        review_summary = None
    try:
        import_summary = get_import_dashboard_summary()
    except AdminDashboardSummaryError:
        import_summary = None
    try:
        barangay_summary = get_active_barangay_priority_summary()
    except AssessmentQueryError:
        barangay_summary = None
    return render_dashboard(
        "System Administrator", with_reviews=False, initial_setup=initial_setup,
        pending_reset_count=reset_count, is_admin_dashboard=True,
        staff_account_summary=account_summary,
        admin_review_summary=review_summary,
        import_dashboard_summary=import_summary,
        admin_barangay_summary=barangay_summary,
    )


@main.get("/administration/profile")
@admin_required
def admin_profile():
    """Show the administrator's profile and credential settings."""
    require_fixture_source()
    return render_template(
        "staff/admin_profile.html", role_label="System Administrator",
        staff_username=g.user["username"], csrf_token=csrf_token(),
    )


def render_staff_profile_error(message=None):
    response = render_template(
        "staff/staff_profile.html", role_label="LGU Staff",
        staff_username=g.user["username"],
        staff_display_name=g.user.get("display_name") or g.user["username"],
        csrf_token=csrf_token(), error=message,
    )
    return (response, 400) if message else response


@main.get("/profile")
@lgu_staff_required
def staff_profile():
    """Show LGU Staff identity and self-service password settings."""
    require_fixture_source()
    return render_staff_profile_error()


@main.post("/profile/password")
@lgu_staff_required
def staff_profile_password():
    """Apply a signed-in LGU Staff password change immediately."""
    require_fixture_source()
    check_csrf()
    try:
        updated_user = change_staff_password(
            g.user, request.form.get("current_password", ""),
            request.form.get("new_password", ""),
            request.form.get("confirm_password", ""),
        )
    except StaffProfileInputError as exc:
        return render_staff_profile_error(str(exc))
    except (psycopg.Error, RuntimeError):
        abort(503, description="Password changes are unavailable. Please try again.")
    start_session(updated_user)
    flash("Password changed successfully.", "profile")
    return redirect(url_for("main.staff_profile"))


def render_admin_profile_error(message, *, username_value=None, password_error=False):
    return render_template(
        "staff/admin_profile.html", role_label="System Administrator",
        staff_username=g.user["username"], csrf_token=csrf_token(),
        username_value=username_value, error=message,
        error_section="password" if password_error else "username",
    ), 400


@main.post("/administration/profile/username")
@admin_required
def admin_profile_username():
    """Apply an administrator username change immediately."""
    require_fixture_source()
    check_csrf()
    username = request.form.get("username", "")
    try:
        updated_user, new_username = change_admin_username(g.user, username)
    except AdminProfileInputError as exc:
        return render_admin_profile_error(str(exc), username_value=username)
    except (psycopg.Error, RuntimeError):
        abort(503, description="Username changes are unavailable. Please try again.")
    start_session(updated_user)
    flash(
        f"Username changed to {new_username}. Remember this username for your next sign-in.",
        "profile",
    )
    return redirect(url_for("main.admin_profile"))


@main.post("/administration/profile/password")
@admin_required
def admin_profile_password():
    """Apply an administrator password change immediately."""
    require_fixture_source()
    check_csrf()
    try:
        updated_user = change_admin_password(
            g.user, request.form.get("current_password", ""),
            request.form.get("new_password", ""),
            request.form.get("confirm_password", ""),
        )
    except AdminProfileInputError as exc:
        return render_admin_profile_error(str(exc), password_error=True)
    except (psycopg.Error, RuntimeError):
        abort(503, description="Password changes are unavailable. Please try again.")
    start_session(updated_user)
    flash(
        "Password changed. Remember your new password for your next sign-in.",
        "profile",
    )
    return redirect(url_for("main.admin_profile"))


@main.route("/administration/import", methods=["GET", "POST"])
@admin_required
def data_import():
    """Validate four historical CSV sources without changing a dataset batch."""
    require_fixture_source()
    report = None
    error = None
    batch_id = None
    survey_year_value = ""
    status = 200
    action = "check"
    preview_token = None
    initial_setup = True
    stored_year_counts = {}
    if current_app.config.get("DATABASE_URL"):
        try:
            initial_setup = not active_dataset_exists()
        except BatchActivationError:
            abort(503, description="Dataset setup status is unavailable. Please try again.")
    if request.method == "POST":
        request.max_content_length = current_app.config["IMPORT_MAX_CONTENT_LENGTH"]
        try:
            check_csrf()
            survey_year_value = request.form.get("survey_year", "")
            action = request.form.get("action", "check")
            if action not in ("check", "store"):
                abort(400, description="Choose a valid data import action.")
            try:
                survey_year = parse_survey_year(survey_year_value)
            except ValueError:
                error = (
                    f"Choose a supported survey year: every two years from "
                    f"{MIN_SURVEY_YEAR} through {latest_survey_year()}."
                )
                status = 400
            else:
                expected_preview_digest = None
                if action == "store":
                    try:
                        expected_preview_digest = verify_import_preview_token(
                            request.form.get("preview_token", ""),
                            g.user["id"], survey_year,
                        )
                    except ImportPreviewError as exc:
                        error = str(exc)
                        status = 400
                if error:
                    result = None
                else:
                    result = validate_admin_upload(
                        request.files, g.user["id"], survey_year,
                        store_inactive=action == "store",
                        authorization_confirmed=request.form.get("authorized_source") == "yes",
                        same_year_confirmed=request.form.get("confirm_same_year") == "yes",
                        expected_preview_digest=expected_preview_digest,
                    )
                if result is not None:
                    report = result["report"]
                    error = result["error"]
                    batch_id = result.get("batch_id")
                    if (action == "check" and report and report["passed"]
                            and not error and result.get("preview_digest")):
                        preview_token = create_import_preview_token(
                            g.user["id"], survey_year, result["preview_digest"],
                        )
                    if error or (report and not report["passed"]):
                        status = 400
        except RequestEntityTooLarge:
            try:
                record_oversized_attempt(g.user["id"])
            except ImportValidationUnavailable:
                abort(503, description="Data validation is unavailable. Please try again.")
            error = "The four files exceed the upload limit of about 268 MB."
            status = 413
        except ImportValidationUnavailable as exc:
            if str(exc) == "system_reference_package_unavailable":
                error = (
                    "The system reference package is unavailable. Ask the system "
                    "installer to configure the approved codebook and mappings."
                )
            elif str(exc) == "import_storage_unavailable":
                error = (
                    "The files passed validation, but the dataset could not be stored. "
                    "No existing data was changed. Try again."
                )
            else:
                error = (
                    "The files could not be validated because the import service is "
                    "temporarily unavailable. Nothing was stored. Try again."
                )
            status = 503
    if current_app.config.get("DATABASE_URL"):
        try:
            stored_year_counts = stored_survey_year_counts()
        except ImportValidationUnavailable:
            abort(503, description="Dataset setup status is unavailable. Please try again.")
    presented_report = present_report(report)
    import_result = present_import_result(presented_report, batch_id)
    if (request.method == "POST" and action == "check"
            and request.headers.get("X-Requested-With") == "fetch"):
        return jsonify({
            "preview_html": render_template(
                "staff/_import_preview.html",
                report=presented_report, error=error, batch_id=None,
                survey_year_value=survey_year_value,
                selected_year_count=(
                    stored_year_counts.get(int(survey_year_value), 0)
                    if survey_year_value.isdecimal() else 0
                ),
                preview_token=preview_token,
                allow_confirmation=bool(preview_token),
                interactive_preview=True,
                import_result=None,
                import_action="check",
                initial_setup=initial_setup,
            ),
        }), status
    import_history = []
    history_error = None
    if current_app.config.get("DATABASE_URL"):
        try:
            import_history = get_import_history(limit=25)
        except ImportHistoryUnavailable:
            history_error = "Import history is temporarily unavailable."
    return render_template(
        "staff/data_import.html", report=presented_report, error=error,
        batch_id=batch_id, survey_year_value=survey_year_value,
        survey_year_options=supported_survey_years(),
        stored_year_counts=stored_year_counts,
        max_upload_file_bytes=FILE_UPLOAD_BYTES,
        max_upload_file_mb=FILE_UPLOAD_MB,
        preview_token=preview_token, allow_confirmation=False,
        import_result=import_result,
        import_action=action,
        initial_setup=initial_setup,
        import_history=import_history, history_error=history_error,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), status


@main.get("/administration/import/history.csv")
@admin_required
def import_history_export():
    """Download retained aggregate import history without source record contents."""
    require_fixture_source()
    try:
        contents = export_import_history()
    except ImportHistoryUnavailable:
        abort(503, description="Import history is unavailable. Please try again.")
    return Response(
        "\ufeff" + contents,
        content_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                'attachment; filename="tanglaw-buhay-import-history.csv"'
            ),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


@main.post("/administration/import/batches/<int:batch_id>/delete")
@admin_required
def delete_stored_import_batch(batch_id):
    """Permanently remove one unused inactive import after explicit confirmation."""
    require_fixture_source()
    check_csrf()
    confirmations = request.form.getlist("delete_confirmation")
    if len(confirmations) != 1:
        abort(400, description="Enter the required batch deletion confirmation.")
    try:
        result = delete_import_batch(batch_id, g.user["id"], confirmations[0])
    except BatchDeletionError as exc:
        code = str(exc)
        if code == "invalid_deletion_request":
            abort(400, description=f"Type DELETE BATCH {batch_id} to confirm deletion.")
        if code == "unauthorized":
            abort(403)
        if code == "deletion_unavailable":
            abort(503, description="The stored batch could not be deleted. Try again later.")
        messages = {
            "active_batch": "The active dataset cannot be deleted.",
            "batch_missing": "This stored batch no longer exists.",
            "batch_not_deletable": "This batch is not an administrator-imported stored batch.",
            "batch_busy": "Another import or assessment preparation is in progress. Try again later.",
            "audit_missing": "This batch cannot be deleted because its import audit is incomplete.",
            "batch_referenced": "This batch is used as a reference by another import and cannot be deleted.",
            "retained_history": "This batch has retained activation, staff-review, or citizen-account history and cannot be deleted.",
        }
        flash(messages.get(code, "This stored batch cannot be deleted."), "import_error")
    else:
        flash(
            f"Batch {result['batch_id']} for survey year {result['survey_year']} was "
            f"permanently deleted with {result['households']} household records and "
            f"{result['assessment_runs']} assessment runs. Its deletion audit was retained.",
            "import_success",
        )
    return redirect(url_for("main.import_batches"), code=303)


@main.route("/administration/import/activation", methods=["GET", "POST"])
@admin_required
def import_activation():
    """Switch to a prepared batch only after fresh review and run checks."""
    require_fixture_source()
    error = None
    status = 200
    if request.method == "POST":
        check_csrf()
        target_value = request.form.get("target_batch_id", "")
        target_run_value = request.form.get("expected_target_run_id", "")
        active_value = request.form.get("expected_active_batch_id", "")
        active_run_value = request.form.get("expected_active_run_id", "")
        active_values_match = (
            (not active_value and not active_run_value)
            or (active_value.isascii() and active_value.isdecimal()
                and active_run_value.isascii() and active_run_value.isdecimal()
                and len(active_value) <= 18 and len(active_run_value) <= 18)
        )
        if (request.form.get("confirm_switch") != "yes"
                or not target_value.isascii() or not target_value.isdecimal()
                or len(target_value) > 18
                or not target_run_value.isascii() or not target_run_value.isdecimal()
                or len(target_run_value) > 18 or not active_values_match):
            error = "Select a prepared batch and confirm the switch."
            status = 400
        else:
            try:
                result = activate_prepared_batch(
                    int(target_value), g.user["id"],
                    expected_active_batch_id=(int(active_value) if active_value else None),
                    expected_active_run_id=(int(active_run_value) if active_run_value else None),
                    expected_target_run_id=int(target_run_value),
                )
            except BatchActivationError as exc:
                if str(exc) == "unfinished_reviews":
                    error = (
                        f"{exc.review_count} staff review"
                        f"{' is' if exc.review_count == 1 else 's are'} still In Review. "
                        "Finish or cancel them before switching batches."
                    )
                    status = 409
                elif str(exc) == "citizen_work_open":
                    error = (
                        "Batch switching is blocked. This dataset still has "
                        f"{describe_citizen_work(exc.account_count, exc.request_count)}. "
                        "Ask LGU Staff to deactivate the accounts and accept or cancel the "
                        "requests in Submissions first."
                    )
                    status = 409
                elif str(exc) in (
                        "selection_changed", "batch_not_ready", "batch_not_switchable"):
                    error = "The selected batch changed or is not ready. Reload and review it again."
                    status = 409
                elif str(exc) == "unauthorized":
                    abort(403)
                elif str(exc) == "invalid_switch_request":
                    error = "Select a valid prepared batch."
                    status = 400
                else:
                    abort(503, description="Batch switching is unavailable. Please try again.")
            else:
                if result["action"] == "initial_activate":
                    flash(
                        f"Batch {result['target_batch_id']} is now the first active dataset."
                    )
                else:
                    flash(
                        f"Batch {result['target_batch_id']} is now active. "
                        f"Batch {result['previous_batch_id']} and its review history remain stored.",
                    )
                return redirect(url_for("main.import_activation"))
    try:
        overview = activation_overview()
    except BatchActivationError:
        abort(503, description="Prepared batches are unavailable. Please try again.")
    return render_template(
        "staff/import_activation.html", overview=overview, error=error,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), status


@main.route("/administration/accounts", methods=["GET", "POST"])
@main.route("/administration/accounts/create", methods=["GET", "POST"],
            endpoint="staff_account_create")
@admin_required
def staff_accounts():
    require_fixture_source()
    show_create = request.endpoint == "main.staff_account_create"
    error = None
    created_account = None
    created_delivery_sent = False
    created_delivery_error = None
    revealed_account = None
    display_name = ""
    username = ""
    if request.method == "POST":
        check_csrf()
        display_name = request.form.get("display_name", "")
        username = request.form.get("username", "")
        try:
            action = request.form.get("action")
            if action is None and (show_create or "display_name" in request.form):
                action = "create"
            if action == "create":
                delivery_method = request.form.get("delivery_method", "display")
                if delivery_method not in ("display", "email"):
                    raise StaffAccountInputError("Choose how to give the temporary password.")
                recipient = None
                if delivery_method == "email":
                    recipient = prepare_temporary_password_email(
                        request.form.get("recipient_email", ""),
                        request.form.get("confirm_recipient_email", ""),
                        request.form.get("verified_in_person", ""),
                    )
                created_account = create_staff_account(display_name, username)
                show_create = True
                if recipient:
                    try:
                        send_staff_temporary_password_email(
                            recipient, created_account["temporary_password"],
                            account_action="created",
                        )
                        created_delivery_sent = True
                    except EmailDeliveryError as exc:
                        created_delivery_error = str(exc)
            elif action == "reveal" and not show_create:
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
            abort(503, description="This account action is unavailable. Please try again.")
    try:
        accounts = [] if show_create else get_staff_accounts()
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff accounts are unavailable. Please try again.")
    return render_template(
        "staff/staff_accounts.html", error=error, created_account=created_account,
        created_delivery_sent=created_delivery_sent,
        created_delivery_error=created_delivery_error,
        accounts=accounts, revealed_account=revealed_account, show_create=show_create,
        display_name=display_name, username=username,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    ), (400 if error else 200)


def render_staff_access_page(account_id, *, selected=None, error=None, status=200):
    """Render one account's access selection and recent administrator changes."""
    try:
        access = get_staff_access(account_id)
        history = get_staff_access_history(account_id)
    except StaffAccessInputError:
        abort(404, description="This staff account is unavailable.")
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff access management is unavailable. Apply migration 0021.")
    presets = [
        {
            "key": key,
            "label": ACCESS_PRESET_LABELS[key],
            "permissions": sorted(permissions),
        }
        for key, permissions in ACCESS_PRESETS.items()
    ]
    return render_template(
        "staff/staff_access.html", access=access, history=history,
        selected=set(access["selected"] if selected is None else selected),
        presets=presets, error=error, role_label="System Administrator",
        staff_username=g.user["username"], csrf_token=csrf_token(),
    ), status


@main.route("/administration/accounts/<int:account_id>/access", methods=["GET", "POST"])
@admin_required
def staff_account_access(account_id):
    """View or replace an active LGU Staff account's module permissions."""
    require_fixture_source()
    if request.method == "GET":
        return render_staff_access_page(account_id)
    check_csrf()
    selected = request.form.getlist("permissions")
    try:
        result = update_staff_access(g.user["id"], account_id, selected)
    except StaffAccessInputError as exc:
        return render_staff_access_page(
            account_id, selected=selected, error=str(exc), status=400,
        )
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff access management is unavailable. Apply migration 0021.")
    flash(
        "Staff access updated. The staff member must sign in again."
        if result["changed"] else "Staff access was already up to date.",
        "staff_access",
    )
    return redirect(url_for("main.staff_account_access", account_id=account_id), code=303)


def render_account_list_dialog(template, *, status=200, **dialog_context):
    """Keep account confirmations and results over the staff directory."""
    try:
        accounts = get_staff_accounts()
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff accounts are unavailable. Please try again.")
    return render_template(
        "staff/staff_accounts.html", error=None, created_account=None,
        accounts=accounts, revealed_account=None, show_create=False,
        display_name="", username="", action_dialog_template=template,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(), **dialog_context,
    ), status


@main.route("/administration/accounts/<int:account_id>/deactivate",
            methods=["GET", "POST"])
@admin_required
def staff_account_deactivate(account_id):
    require_fixture_source()
    if request.method == "POST":
        check_csrf()
        try:
            deactivate_staff_account(account_id)
        except StaffAccountHasActiveReviews as exc:
            try:
                account = get_staff_account_to_deactivate(account_id)
            except StaffAccountInputError:
                abort(404, description="This staff account is no longer active.")
            except (psycopg.Error, RuntimeError):
                abort(503, description="Staff accounts are unavailable. Please try again.")
            return render_account_list_dialog(
                "staff/deactivate_staff_account.html", account=account,
                action_error=str(exc), status=409,
            )
        except StaffAccountInputError:
            abort(404, description="This staff account is no longer active.")
        except (psycopg.Error, RuntimeError):
            abort(503, description="This account action is unavailable. Please try again.")
        flash("Staff account deactivated.")
        return redirect(url_for("main.staff_accounts"))

    try:
        account = get_staff_account_to_deactivate(account_id)
    except StaffAccountInputError:
        abort(404, description="This staff account is no longer active.")
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff accounts are unavailable. Please try again.")
    return render_account_list_dialog(
        "staff/deactivate_staff_account.html", account=account,
        action_error=None,
    )


def staff_account_recovery(account_id, *, reactivate):
    """Confirm and complete one administrator credential action."""
    require_fixture_source()
    delivery_error = None
    delivery_sent = False
    form_error = None
    try:
        if request.method == "POST":
            check_csrf()
            delivery_method = request.form.get("delivery_method", "display")
            if delivery_method not in ("display", "email"):
                raise StaffAccountInputError("Choose how to give the temporary password.")
            recipient = None
            if delivery_method == "email":
                recipient = prepare_temporary_password_email(
                    request.form.get("recipient_email", ""),
                    request.form.get("confirm_recipient_email", ""),
                    request.form.get("verified_in_person", ""),
                )
            account = recover_staff_account(
                account_id, reactivate=reactivate, administrator_id=g.user["id"],
                delivery_method=("email" if delivery_method == "email"
                                 else "direct_handoff"),
            )
            completed = True
            if recipient:
                try:
                    send_staff_temporary_password_email(
                        recipient, account["temporary_password"],
                        account_action=("reactivated" if reactivate else "reset"),
                    )
                    delivery_sent = True
                    try:
                        mark_reset_request_email_accepted(
                            account["completed_request_ids"],
                        )
                    except (psycopg.Error, RuntimeError):
                        pass  # Sending succeeded; audit recovery must not hide the credential result.
                except EmailDeliveryError as exc:
                    delivery_error = str(exc)
        else:
            account = get_staff_account_for_recovery(account_id, reactivate=reactivate)
            completed = False
    except StaffAccountInputError as exc:
        if request.method == "POST":
            try:
                account = get_staff_account_for_recovery(account_id, reactivate=reactivate)
            except StaffAccountInputError:
                abort(404, description="This staff account is not available for this action.")
            completed = False
            form_error = str(exc)
        else:
            abort(404, description="This staff account is not available for this action.")
    except (psycopg.Error, RuntimeError):
        abort(503, description="This account action is unavailable. Please try again.")
    return render_account_list_dialog(
        "staff/recover_staff_account.html", account=account,
        reactivate=reactivate, completed=completed, form_error=form_error,
        delivery_sent=delivery_sent, delivery_error=delivery_error,
        recipient_email="", confirm_recipient_email="",
        return_to_requests=request.args.get("source") == "requests",
        status=(400 if form_error else 200),
    )


@main.get("/administration/accounts/password-reset-requests")
@admin_required
def staff_password_reset_requests():
    """Show pending Staff Portal password help and recent completion history."""
    require_fixture_source()
    try:
        overview = password_reset_request_overview()
    except (psycopg.Error, RuntimeError):
        abort(503, description="Password reset requests are unavailable. Apply migration 0020.")
    return render_template(
        "staff/password_reset_requests.html", overview=overview,
        role_label="System Administrator", staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@main.route("/administration/accounts/<int:account_id>/reset-password",
            methods=["GET", "POST"])
@admin_required
def staff_account_reset_password(account_id):
    return staff_account_recovery(account_id, reactivate=False)


@main.route("/administration/accounts/<int:account_id>/reactivate",
            methods=["GET", "POST"])
@admin_required
def staff_account_reactivate(account_id):
    return staff_account_recovery(account_id, reactivate=True)


@main.get("/households/<path:retired_path>")
@main.get("/assessments/<path:retired_path>")
def retired_staff_pages(retired_path=None):
    """Retire old bookmarks without looking up or echoing any household information."""
    abort(410, description="This staff page has been retired while the interface is rebuilt.")


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
