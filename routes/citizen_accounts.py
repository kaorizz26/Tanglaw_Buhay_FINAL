"""Staff pages for creating Citizen Accounts and resending activation.

Only LGU Staff may use these pages. The activation link is emailed by the
service and never appears here; staff see only the six-digit code, once.
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from routes.staff_forms import (
    ALREADY_DONE_MESSAGE, context_ids, issue_form_number, staff_context, use_form_number,
)
from services.assessment_queries import AssessmentQueryError
from services.auth import check_csrf, lgu_staff_required
from services.citizen_accounts import (
    CitizenAccountError, CitizenAccountInputError, change_account_email,
    create_citizen_account, deactivate_account, get_household_account_page,
    reactivate_account, resend_activation,
)

citizen_accounts = Blueprint("citizen_accounts", __name__)

FORM_FIELDS = (
    "representative_name", "email", "email_confirmation", "representative_verified",
    "privacy_consent_confirmed", "verification_note",
)
EMAIL_IN_USE_MESSAGE = (
    "This email address is already used by another Citizen Account. "
    "Check the address, or use a different one."
)
# Shown on the Assessment page when the account's status changed after a form was opened.
STATE_CHANGED_MESSAGES = {
    "account_missing": "This household has no Citizen Account yet.",
    "not_deactivatable": "This account is already deactivated.",
    "not_deactivated": "Only a Deactivated account can be reactivated.",
    "deactivated": "Reactivate the account before changing its email address.",
}


def _load_page(household_uuid, **options):
    """Read the household and its account state, or stop with a clear error page."""
    try:
        return get_household_account_page(str(household_uuid), **options)
    except AssessmentQueryError as failure:
        if str(failure) in ("household_assessment_missing", "household_identity_missing"):
            abort(404, description="This household assessment is unavailable in the selected batch.")
        abort(503, description="Citizen accounts are unavailable. Please try again later.")


def _stop_for(failure):
    """Turn a fixed service error code into the matching error page."""
    code = str(failure)
    if code == "unauthorized":
        abort(403, description="Your account cannot create or manage Citizen Accounts.")
    if code == "household_missing":
        abort(404, description="This household assessment is unavailable in the selected batch.")
    if code in ("invalid_request", "context_changed"):
        abort(400, description=(
            "The household selection changed or the form expired. "
            "Return to the Assessment page and try again."
        ))
    abort(503, description=(
        "The Citizen Account could not be saved. Return to the Assessment page "
        "to check its status before trying again."
    ))


def _assessment_url(household_uuid):
    return url_for("assessments.detail", household_uuid=household_uuid)


def _redirect_to_assessment(household_uuid, message):
    # The Assessment page shows this message in its Citizen Account section.
    flash(message, "citizen_account")
    return redirect(_assessment_url(household_uuid), code=303)


def _render_form(page, household_uuid, values, errors, status=200):
    return render_template(
        "citizen_accounts/create.html", page=page, values=values, errors=errors,
        form_number=issue_form_number(),
        assessment_url=_assessment_url(household_uuid), **staff_context(),
    ), status


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/new", methods=["GET", "POST"],
)
@lgu_staff_required
def create(household_uuid):
    """Show the Create Citizen Account form and save it."""
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return _redirect_to_assessment(household_uuid, ALREADY_DONE_MESSAGE)
    page = _load_page(household_uuid)
    state = page["account"]["state"]
    if state == "unavailable":
        abort(503, description="Citizen accounts are unavailable. Please try again later.")
    if state != "none":
        return _redirect_to_assessment(
            household_uuid, "This household already has a Citizen Account.",
        )
    if request.method == "GET":
        return _render_form(page, household_uuid, {}, {})
    batch_id, run_id = context_ids()
    values = {key: request.form.get(key, "") for key in FORM_FIELDS}
    try:
        result = create_citizen_account(
            g.user["id"], str(household_uuid), batch_id, run_id, request.form,
        )
    except CitizenAccountInputError as invalid:
        return _render_form(page, household_uuid, values, invalid.errors, 400)
    except CitizenAccountError as failure:
        if str(failure) == "household_has_account":
            return _redirect_to_assessment(
                household_uuid, "This household already has a Citizen Account.",
            )
        if str(failure) == "email_in_use":
            return _render_form(
                page, household_uuid, values, {"email": EMAIL_IN_USE_MESSAGE}, 409,
            )
        _stop_for(failure)
    return render_template(
        "citizen_accounts/result.html", result=result, page=page,
        heading="Citizen Account created",
        assessment_url=_assessment_url(household_uuid), **staff_context(),
    )


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/resend", methods=["GET", "POST"],
)
@lgu_staff_required
def resend(household_uuid):
    """Confirm and send a new activation link and code, replacing the old pair."""
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return _redirect_to_assessment(household_uuid, ALREADY_DONE_MESSAGE)
    page = _load_page(household_uuid)
    account = page["account"]
    if account["state"] == "unavailable":
        abort(503, description="Citizen accounts are unavailable. Please try again later.")
    if account["state"] == "none":
        return _redirect_to_assessment(household_uuid, "This household has no Citizen Account yet.")
    if not account["can_resend"]:
        return _redirect_to_assessment(
            household_uuid, "Activation can only be resent while the account is Pending Activation.",
        )
    if request.method == "GET":
        return render_template(
            "citizen_accounts/resend.html", page=page, form_number=issue_form_number(),
            assessment_url=_assessment_url(household_uuid), **staff_context(),
        )
    batch_id, run_id = context_ids()
    try:
        result = resend_activation(g.user["id"], str(household_uuid), batch_id, run_id)
    except CitizenAccountError as failure:
        if str(failure) == "not_pending":
            return _redirect_to_assessment(
                household_uuid,
                "Activation can only be resent while the account is Pending Activation.",
            )
        if str(failure) == "account_missing":
            return _redirect_to_assessment(
                household_uuid, "This household has no Citizen Account yet.",
            )
        _stop_for(failure)
    return render_template(
        "citizen_accounts/result.html", result=result, page=page,
        heading="Activation email resent",
        assessment_url=_assessment_url(household_uuid), **staff_context(),
    )


def _manageable_page(household_uuid, permission, blocked_message):
    """Load the page and check the account allows this action.

    Returns (page, None), or (page, redirect) when staff should go back to the
    Assessment page with a message.
    """
    page = _load_page(household_uuid)
    account = page["account"]
    if account["state"] == "unavailable":
        abort(503, description="Citizen accounts are unavailable. Please try again later.")
    if account["state"] == "none":
        return page, _redirect_to_assessment(
            household_uuid, "This household has no Citizen Account yet.",
        )
    if not account[permission]:
        return page, _redirect_to_assessment(household_uuid, blocked_message)
    return page, None


def _management_form(household_uuid, *, template, permission, blocked_message, fields,
                     act, finish):
    """Show a management form (GET) and save it (POST) for deactivate, reactivate and email change.

    `act` runs the service call; `finish` builds the response after it succeeds.
    """
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return _redirect_to_assessment(household_uuid, ALREADY_DONE_MESSAGE)
    page, stop = _manageable_page(household_uuid, permission, blocked_message)
    if stop:
        return stop

    def render_form(values, errors, status=200):
        return render_template(
            template, page=page, values=values, errors=errors,
            form_number=issue_form_number(),
            assessment_url=_assessment_url(household_uuid), **staff_context(),
        ), status

    if request.method == "GET":
        return render_form({}, {})
    batch_id, run_id = context_ids()
    values = {key: request.form.get(key, "") for key in fields}
    try:
        result = act(g.user["id"], str(household_uuid), batch_id, run_id, request.form)
    except CitizenAccountInputError as invalid:
        return render_form(values, invalid.errors, 400)
    except CitizenAccountError as failure:
        code = str(failure)
        if code in STATE_CHANGED_MESSAGES:
            return _redirect_to_assessment(household_uuid, STATE_CHANGED_MESSAGES[code])
        if code == "email_in_use":
            return render_form(values, {"email": EMAIL_IN_USE_MESSAGE}, 409)
        _stop_for(failure)
    return finish(result, page)


def _show_result(heading):
    """Build the page that shows the activation code once after an email was sent."""
    def finish(result, page):
        return render_template(
            "citizen_accounts/result.html", result=result, page=page, heading=heading,
            assessment_url=_assessment_url(page["household_uuid"]), **staff_context(),
        )
    return finish


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/deactivate", methods=["GET", "POST"],
)
@lgu_staff_required
def deactivate(household_uuid):
    """Confirm and deactivate the account."""
    def finish(result, page):
        return _redirect_to_assessment(
            household_uuid, "The Citizen Account was deactivated. The representative is signed out.",
        )
    return _management_form(
        household_uuid, template="citizen_accounts/deactivate.html",
        permission="can_deactivate", blocked_message=STATE_CHANGED_MESSAGES["not_deactivatable"],
        fields=("reason_note",), act=deactivate_account, finish=finish,
    )


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/reactivate", methods=["GET", "POST"],
)
@lgu_staff_required
def reactivate(household_uuid):
    """Confirm and reactivate a Deactivated account with a new activation email."""
    return _management_form(
        household_uuid, template="citizen_accounts/reactivate.html",
        permission="can_reactivate", blocked_message=STATE_CHANGED_MESSAGES["not_deactivated"],
        fields=("representative_verified", "privacy_consent_confirmed", "verification_note"),
        act=reactivate_account, finish=_show_result("Citizen Account reactivated"),
    )


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/change-email", methods=["GET", "POST"],
)
@lgu_staff_required
def change_email(household_uuid):
    """Confirm and change the registered email, then require activation again."""
    return _management_form(
        household_uuid, template="citizen_accounts/change_email.html",
        permission="can_change_email", blocked_message=STATE_CHANGED_MESSAGES["deactivated"],
        fields=("email", "email_confirmation", "representative_verified",
                "privacy_consent_confirmed", "verification_note"),
        act=change_account_email, finish=_show_result("Email address changed"),
    )


@citizen_accounts.route("/citizen-accounts/household/<uuid:household_uuid>/history")
@lgu_staff_required
def history(household_uuid):
    """Show the account's staff actions, newest first."""
    page = _load_page(household_uuid, with_history=True)
    state = page["account"]["state"]
    if state == "unavailable":
        abort(503, description="Citizen accounts are unavailable. Please try again later.")
    if state == "none":
        return _redirect_to_assessment(household_uuid, "This household has no Citizen Account yet.")
    return render_template(
        "citizen_accounts/history.html", page=page,
        assessment_url=_assessment_url(household_uuid), **staff_context(),
    )
