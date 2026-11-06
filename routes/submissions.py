"""Staff pages for Submissions: a directory of document requests and one of Citizen Accounts.

Only LGU Staff may use these pages, and they only read. Creating accounts, requesting
documents and reviewing files still happen on the existing pages these lists link to.
"""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from routes.staff_forms import staff_context
from services.assessment_queries import AssessmentQueryError
from services.auth import lgu_staff_required
from services.submissions import (
    STATUS_OPTIONS, InvalidDirectoryFilters, get_account_detail, get_account_directory,
    get_request_directory, parse_directory_arguments, return_parameters,
)

submissions = Blueprint("submissions", __name__, url_prefix="/submissions")

BAD_FILTERS_MESSAGE = "Choose a valid search, status or barangay."
UNAVAILABLE_MESSAGE = "This list is unavailable right now. Please try again later."


def _read_arguments(kind):
    """Read the search, status, barangay and page from the address, or stop with an error."""
    try:
        return parse_directory_arguments(request.args, dict(STATUS_OPTIONS[kind]))
    except InvalidDirectoryFilters:
        abort(400, description=BAD_FILTERS_MESSAGE)


def _read_directory(read, page, filters):
    """Run a directory read, turning its failures into clear error pages."""
    try:
        results = read(page, filters)
    except InvalidDirectoryFilters:
        abort(400, description=BAD_FILTERS_MESSAGE)
    except AssessmentQueryError:
        abort(503, description=UNAVAILABLE_MESSAGE)
    if results is None:
        abort(404, description="This page of the list does not exist.")
    return results


@submissions.get("/document-requests")
@lgu_staff_required
def document_requests():
    """List every document request; requests waiting for review come first."""
    page, filters = _read_arguments("document_requests")
    results = _read_directory(get_request_directory, page, filters)
    return render_template(
        "submissions/document_requests.html", results=results,
        return_parameters=return_parameters(page, filters), **staff_context(),
    )


@submissions.get("/citizen-accounts")
@lgu_staff_required
def citizen_accounts():
    """List every Citizen Account with its status."""
    page, filters = _read_arguments("citizen_accounts")
    results = _read_directory(get_account_directory, page, filters)
    return render_template(
        "submissions/citizen_accounts.html", results=results,
        return_parameters=return_parameters(page, filters), **staff_context(),
    )


@submissions.get("/citizen-accounts/household/<uuid:household_uuid>")
@lgu_staff_required
def account_detail(household_uuid):
    """Show one Citizen Account with the existing account actions and its document requests."""
    page, filters = _read_arguments("citizen_accounts")
    try:
        results = get_account_detail(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) in ("household_assessment_missing", "household_identity_missing"):
            abort(404, description="This household is not in the selected survey.")
        abort(503, description=UNAVAILABLE_MESSAGE)
    if results["citizen_account"]["state"] == "none":
        return redirect(url_for("submissions.citizen_accounts", **filters.parameters), code=303)
    return render_template(
        "submissions/account_detail.html", results=results,
        list_url=url_for("submissions.citizen_accounts", page=page, **filters.parameters),
        assessment_url=url_for("assessments.detail", household_uuid=household_uuid),
        **staff_context(),
    )
