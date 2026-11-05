"""Staff pages for requesting documents from a household, viewing requests and cancelling them.

Only LGU Staff may use these pages. Requests are made only for households with an
Active Citizen Account. Staff decisions here never change assessments or Staff Reviews.
"""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from routes.staff_forms import context_ids, issue_form_number, staff_context, use_form_number
from services.assessment_queries import AssessmentQueryError
from services.auth import check_csrf, lgu_staff_required
from services.citizen_accounts import CitizenAccountError, get_household_account_page
from services.document_requests import (
    MAX_DOCUMENTS_PER_FORM, DocumentRequestInputError, cancel_document_request,
    create_document_requests, get_document_request_page,
)

document_requests = Blueprint("document_requests", __name__)

# Messages with this category appear in the Document Requests section of the Assessment page.
FLASH_CATEGORY = "document_requests"
MIN_FORM_ROWS = 3
BLANK_ROW = {"title": "", "instructions": ""}
UNAVAILABLE_MESSAGE = "Document requests are unavailable. Please try again later."
NOT_ACTIVE_MESSAGE = "Documents can only be requested while the Citizen Account is Active."
REQUEST_GONE_MESSAGE = "This document request is no longer available."
NOT_CANCELLABLE_MESSAGE = "This request can no longer be cancelled."
ALREADY_DONE_MESSAGE = "This request was already completed, so nothing was changed."
HOUSEHOLD_GONE_MESSAGE = "This household assessment is unavailable in the selected batch."


def _assessment_url(household_uuid):
    return url_for("assessments.detail", household_uuid=household_uuid)


def _redirect_to_assessment(household_uuid, message):
    flash(message, FLASH_CATEGORY)
    return redirect(_assessment_url(household_uuid), code=303)


def _stop_for(failure):
    """Turn a fixed service error code into the matching error page."""
    code = str(failure)
    if code == "unauthorized":
        abort(403, description="Your account cannot request or manage documents.")
    if code in ("household_missing", "request_missing"):
        abort(404, description=HOUSEHOLD_GONE_MESSAGE)
    if code in ("invalid_request", "context_changed"):
        abort(400, description=(
            "The household selection changed or the form expired. "
            "Return to the Assessment page and try again."
        ))
    abort(503, description=UNAVAILABLE_MESSAGE)


def _load_household_page(household_uuid):
    """Read the household and its account state, or stop with a clear error page."""
    try:
        return get_household_account_page(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) in ("household_assessment_missing", "household_identity_missing"):
            abort(404, description=HOUSEHOLD_GONE_MESSAGE)
        abort(503, description=UNAVAILABLE_MESSAGE)


def _load_request_page(request_id):
    """Read a request with its household, or stop with a clear error page."""
    try:
        return get_document_request_page(request_id)
    except AssessmentQueryError as failure:
        if str(failure) in ("document_request_missing", "household_assessment_missing",
                            "household_identity_missing"):
            abort(404, description=REQUEST_GONE_MESSAGE)
        abort(503, description=UNAVAILABLE_MESSAGE)


def _rows_from_form():
    """Rebuild the typed rows after a mistake, keeping at least a few rows on screen."""
    titles = request.form.getlist("document_title")
    instructions = request.form.getlist("document_instructions")
    rows = [
        {"title": title, "instructions": instructions[index] if index < len(instructions) else ""}
        for index, title in enumerate(titles)
    ]
    return rows + [BLANK_ROW] * (MIN_FORM_ROWS - len(rows))


def _render_request_form(page, household_uuid, rows, errors, status=200):
    return render_template(
        "document_requests/create.html", page=page, rows=rows, errors=errors,
        form_number=issue_form_number(), assessment_url=_assessment_url(household_uuid),
        max_rows=MAX_DOCUMENTS_PER_FORM, **staff_context(),
    ), status


@document_requests.route(
    "/document-requests/household/<uuid:household_uuid>/new", methods=["GET", "POST"],
)
@lgu_staff_required
def create(household_uuid):
    """Show the Request Documents form and save every document on it together."""
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return _redirect_to_assessment(household_uuid, ALREADY_DONE_MESSAGE)
    page = _load_household_page(household_uuid)
    state = page["account"]["state"]
    if state == "unavailable":
        abort(503, description=UNAVAILABLE_MESSAGE)
    if state != "active":
        return _redirect_to_assessment(household_uuid, NOT_ACTIVE_MESSAGE)
    if request.method == "GET":
        return _render_request_form(page, household_uuid, [BLANK_ROW] * MIN_FORM_ROWS, {})
    batch_id, run_id = context_ids()
    try:
        count = create_document_requests(
            g.user["id"], str(household_uuid), batch_id, run_id, request.form,
        )
    except DocumentRequestInputError as invalid:
        return _render_request_form(page, household_uuid, _rows_from_form(), invalid.errors, 400)
    except CitizenAccountError as failure:
        if str(failure) in ("account_not_active", "account_missing"):
            return _redirect_to_assessment(household_uuid, NOT_ACTIVE_MESSAGE)
        _stop_for(failure)
    noun = "document" if count == 1 else "documents"
    return _redirect_to_assessment(household_uuid, f"{count} {noun} requested.")


@document_requests.route("/document-requests/<int:request_id>")
@lgu_staff_required
def detail(request_id):
    """Show one request: its status, what to do next and its history."""
    page = _load_request_page(request_id)
    return render_template(
        "document_requests/detail.html", page=page,
        assessment_url=_assessment_url(page["household_uuid"]), **staff_context(),
    )


@document_requests.route("/document-requests/<int:request_id>/cancel", methods=["GET", "POST"])
@lgu_staff_required
def cancel(request_id):
    """Confirm and cancel an open request; the reason is shown to the citizen."""
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return redirect(url_for("document_requests.detail", request_id=request_id), code=303)
    page = _load_request_page(request_id)
    household_uuid = page["household_uuid"]
    if not page["request"]["can_cancel"]:
        return _redirect_to_assessment(household_uuid, NOT_CANCELLABLE_MESSAGE)

    def render_form(errors, status=200):
        return render_template(
            "document_requests/cancel.html", page=page, errors=errors,
            reason=request.form.get("reason_note", ""), form_number=issue_form_number(),
            assessment_url=_assessment_url(household_uuid), **staff_context(),
        ), status

    if request.method == "GET":
        return render_form({})
    batch_id, run_id = context_ids()
    try:
        cancel_document_request(g.user["id"], request_id, batch_id, run_id, request.form)
    except DocumentRequestInputError as invalid:
        return render_form(invalid.errors, 400)
    except CitizenAccountError as failure:
        if str(failure) == "not_cancellable":
            return _redirect_to_assessment(household_uuid, NOT_CANCELLABLE_MESSAGE)
        _stop_for(failure)
    return _redirect_to_assessment(household_uuid, "The document request was cancelled.")
