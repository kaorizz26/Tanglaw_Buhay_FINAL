"""Staff pages for requesting documents, viewing requests, reviewing uploads and cancelling.

Only LGU Staff may use these pages. Requests are made only for households with an
Active Citizen Account. Staff decisions here never change assessments or Staff Reviews.
"""

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for,
)

from routes.staff_forms import context_ids, issue_form_number, staff_context, use_form_number
from services.assessment_queries import AssessmentQueryError
from services.auth import check_csrf, lgu_staff_required
from services.citizen_accounts import CitizenAccountError, get_household_account_page
from services.document_reviews import accept_document, open_file_for_staff, request_correction
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
NOT_REVIEWABLE_MESSAGE = (
    "This document is not waiting for review. It may already have been reviewed or cancelled, "
    "so nothing was changed."
)
NEWER_FILE_MESSAGE = "A newer file was uploaded. Review the latest file below before deciding."
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


# ---- Reviewing an uploaded file ---------------------------------------------------

def _posted_file_id():
    """Read the id of the file the staff member was shown; it is posted back by the form."""
    value = request.form.get("file_id", "")
    if not value.isascii() or not value.isdecimal() or len(value) > 18:
        abort(400, description="The form expired. Return to the request and try again.")
    return int(value)


def _redirect_to_request(request_id, message):
    flash(message, FLASH_CATEGORY)
    return redirect(url_for("document_requests.detail", request_id=request_id), code=303)


@document_requests.get("/document-requests/<int:request_id>/files/<int:file_id>")
@lgu_staff_required
def download_file(request_id, file_id):
    """Download one uploaded file. It is never shown in the browser or served as a static file."""
    try:
        stored = open_file_for_staff(g.user["id"], request_id, file_id)
    except CitizenAccountError as failure:
        _stop_for(failure)
    response = send_file(
        stored["path"], mimetype=stored["content_type"], as_attachment=True,
        download_name=stored["display_name"], max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _review_page(template, request_id, decide, success_message):
    """Show a review confirmation page, or run the decision when it is posted.

    `decide(page, file_id)` calls the service; `success_message` is shown afterwards.
    The page always shows the newest file, and the form posts that file's id back, so a
    file that was replaced meanwhile is noticed.
    """
    if request.method == "POST":
        check_csrf()
        if not use_form_number():
            return redirect(url_for("document_requests.detail", request_id=request_id), code=303)
    page = _load_request_page(request_id)
    if not page["request"]["can_review"] or not page["files"]:
        return _redirect_to_request(request_id, NOT_REVIEWABLE_MESSAGE)
    latest = page["files"][0]

    def render_form(errors, status=200):
        return render_template(
            template, page=page, latest_file=latest, errors=errors,
            reason=request.form.get("reason_note", ""), form_number=issue_form_number(),
            **staff_context(),
        ), status

    if request.method == "GET":
        return render_form({})
    try:
        decide(page, _posted_file_id())
    except DocumentRequestInputError as invalid:
        return render_form(invalid.errors, 400)
    except CitizenAccountError as failure:
        if str(failure) == "file_changed":
            return _redirect_to_request(request_id, NEWER_FILE_MESSAGE)
        if str(failure) == "not_reviewable":
            return _redirect_to_request(request_id, NOT_REVIEWABLE_MESSAGE)
        _stop_for(failure)
    return _redirect_to_request(request_id, success_message)


@document_requests.route("/document-requests/<int:request_id>/accept", methods=["GET", "POST"])
@lgu_staff_required
def accept(request_id):
    """Confirm and accept the newest uploaded file. Accepted is final."""
    def decide(page, file_id):
        batch_id, run_id = context_ids()
        accept_document(g.user["id"], request_id, batch_id, run_id, file_id)

    return _review_page(
        "document_requests/accept.html", request_id, decide, "The document was accepted.",
    )


@document_requests.route("/document-requests/<int:request_id>/correction", methods=["GET", "POST"])
@lgu_staff_required
def correction(request_id):
    """Ask the representative for a corrected file; the reason is shown to them."""
    def decide(page, file_id):
        batch_id, run_id = context_ids()
        request_correction(g.user["id"], request_id, batch_id, run_id, file_id, request.form)

    return _review_page(
        "document_requests/correction.html", request_id, decide,
        "A correction was requested. The representative can now upload a new file.",
    )
