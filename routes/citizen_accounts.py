"""Staff pages for creating Citizen Accounts and resending activation.

Only LGU Staff may use these pages. The activation link is emailed by the
service and never appears here; staff see only the six-digit code, once.
"""

from secrets import token_urlsafe

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, session, url_for,
)

from services.assessment_queries import AssessmentQueryError
from services.auth import check_csrf, csrf_token, lgu_staff_required
from services.citizen_accounts import (
    CitizenAccountError, CitizenAccountInputError, create_citizen_account,
    get_household_account_page, resend_activation,
)

citizen_accounts = Blueprint("citizen_accounts", __name__)

FORM_FIELDS = (
    "representative_name", "email", "email_confirmation", "representative_verified",
    "privacy_consent_confirmed", "verification_note",
)
# Each form carries a one-time number. A repeated POST (a browser refresh or a double
# click) finds it already used and changes nothing, so a resend cannot silently
# replace the code the staff member has just read. The numbers live in the signed
# session cookie, so this prevents ordinary duplicate submissions only; it is not a
# server-side guarantee against someone replaying an older copy of the cookie.
FORM_NUMBERS_KEY = "citizen_account_form_numbers"
MAX_REMEMBERED_FORMS = 10
ALREADY_DONE_MESSAGE = (
    "This request was already completed, so nothing was changed. Activation codes are "
    "shown only once; choose Resend Activation if a new code is needed."
)
EMAIL_IN_USE_MESSAGE = (
    "This email address is already used by another Citizen Account. "
    "Check the address, or use a different one."
)


def _staff_context():
    return {
        "role_label": "LGU Staff", "staff_username": g.user["username"],
        "csrf_token": csrf_token(),
    }


def _issue_form_number():
    """Create a one-time number for a form and remember it in the staff member's session."""
    number = token_urlsafe(16)
    remembered = session.get(FORM_NUMBERS_KEY, [])[-(MAX_REMEMBERED_FORMS - 1):]
    session[FORM_NUMBERS_KEY] = remembered + [number]
    return number


def _use_form_number():
    """Accept the submitted form number once; any repeat or unknown number returns False."""
    remembered = session.get(FORM_NUMBERS_KEY, [])
    submitted = request.form.get("form_number", "")
    if submitted not in remembered:
        return False
    remembered.remove(submitted)
    session[FORM_NUMBERS_KEY] = remembered
    return True


def _load_page(household_uuid):
    """Read the household and its account state, or stop with a clear error page."""
    try:
        return get_household_account_page(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) in ("household_assessment_missing", "household_identity_missing"):
            abort(404, description="This household assessment is unavailable in the selected batch.")
        abort(503, description="Citizen accounts are unavailable. Please try again later.")


def _context_ids():
    """Read the batch and run the page was built from, as hidden numeric fields."""
    ids = []
    for key in ("batch_id", "run_id"):
        values = request.form.getlist(key)
        if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal() \
                or len(values[0]) > 18:
            abort(400, description="The form expired. Return to the Assessment page and try again.")
        ids.append(int(values[0]))
    return ids


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
        form_number=_issue_form_number(),
        assessment_url=_assessment_url(household_uuid), **_staff_context(),
    ), status


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/new", methods=["GET", "POST"],
)
@lgu_staff_required
def create(household_uuid):
    """Show the Create Citizen Account form and save it."""
    if request.method == "POST":
        check_csrf()
        if not _use_form_number():
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
    batch_id, run_id = _context_ids()
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
        assessment_url=_assessment_url(household_uuid), **_staff_context(),
    )


@citizen_accounts.route(
    "/citizen-accounts/household/<uuid:household_uuid>/resend", methods=["GET", "POST"],
)
@lgu_staff_required
def resend(household_uuid):
    """Confirm and send a new activation link and code, replacing the old pair."""
    if request.method == "POST":
        check_csrf()
        if not _use_form_number():
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
            "citizen_accounts/resend.html", page=page, form_number=_issue_form_number(),
            assessment_url=_assessment_url(household_uuid), **_staff_context(),
        )
    batch_id, run_id = _context_ids()
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
        assessment_url=_assessment_url(household_uuid), **_staff_context(),
    )
