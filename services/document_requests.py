"""Staff-side document requests: ask a household for documents, view and cancel requests.

Only LGU Staff reach these functions, and only for households with an Active Citizen
Account. Creating several requests is one transaction, so either every document is
requested or none is. Nothing here touches assessments, subsidy findings or Staff Reviews.
"""

from itertools import zip_longest

import psycopg

from db import get_connection
from repositories import citizen_accounts, document_requests
from services.assessment_queries import AssessmentQueryError, _read_context
from services.citizen_accounts import (
    MAX_NOTE_LENGTH, MIN_NOTE_LENGTH, CitizenAccountError, check_actor_and_context,
    check_request_values, lock_household_account, read_household_header,
)
from services.document_request_panel import (
    OPEN_STATUSES, present_files, present_request, present_request_history,
)

MAX_DOCUMENTS_PER_FORM = 10
MIN_TITLE_LENGTH = 3
MAX_TITLE_LENGTH = 120
MAX_INSTRUCTIONS_LENGTH = 500


class DocumentRequestInputError(ValueError):
    """The form needs correction; `errors` maps field names to plain-English messages.

    Row problems use keys such as "title_0" and "instructions_2" (the row's position).
    """

    def __init__(self, errors):
        super().__init__("invalid_form")
        self.errors = errors


def validate_request_form(form):
    """Return the documents to request as [{"title", "instructions"}], or raise.

    The form repeats `document_title` and `document_instructions` once per row. A row
    with nothing typed is ignored, so staff can leave spare rows empty.
    """
    errors = {}
    documents = []
    seen_titles = set()
    rows = zip_longest(
        form.getlist("document_title"), form.getlist("document_instructions"), fillvalue="",
    )
    for position, (raw_title, raw_instructions) in enumerate(rows):
        title = " ".join(raw_title.split())
        instructions = raw_instructions.strip()
        if not title and not instructions:
            continue
        if not MIN_TITLE_LENGTH <= len(title) <= MAX_TITLE_LENGTH or not title.isprintable():
            errors[f"title_{position}"] = (
                f"Enter a document name of {MIN_TITLE_LENGTH} to {MAX_TITLE_LENGTH} characters."
            )
        elif title.lower() in seen_titles:
            errors[f"title_{position}"] = "This document is already in the list."
        seen_titles.add(title.lower())
        if len(instructions) > MAX_INSTRUCTIONS_LENGTH or "\x00" in instructions:
            errors[f"instructions_{position}"] = (
                f"Keep the instructions under {MAX_INSTRUCTIONS_LENGTH} characters."
            )
        documents.append({"title": title, "instructions": instructions or None})
    if not documents:
        errors["form"] = "Enter the name of at least one document."
    elif len(documents) > MAX_DOCUMENTS_PER_FORM:
        errors["form"] = f"Request up to {MAX_DOCUMENTS_PER_FORM} documents at a time."
    if errors:
        raise DocumentRequestInputError(errors)
    return documents


def validate_reason_form(form):
    """Return the cleaned reason for a cancellation or correction, or raise.

    The citizen will read it, so it must be a real sentence of 10 to 500 characters.
    """
    reason = form.get("reason_note", "").strip()
    if not MIN_NOTE_LENGTH <= len(reason) <= MAX_NOTE_LENGTH or "\x00" in reason:
        raise DocumentRequestInputError({"reason_note": (
            f"Write a reason of {MIN_NOTE_LENGTH} to {MAX_NOTE_LENGTH} characters."
        )})
    return reason


def create_document_requests(staff_id, household_uuid, batch_id, run_id, form, *,
                             connection_factory=get_connection):
    """Request every document on the form for the household's Active account; return the count.

    Raises DocumentRequestInputError for form mistakes and CitizenAccountError with a
    fixed code otherwise (for example "account_not_active"). Nothing is saved if any
    step fails.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    documents = validate_request_form(form)
    try:
        with connection_factory() as connection:
            with connection.transaction():
                account = lock_household_account(
                    connection, staff_id, batch_id, run_id, household_uuid,
                )
                if account["status"] != "active":
                    raise CitizenAccountError("account_not_active")
                for document in documents:
                    created = document_requests.insert_request(
                        connection, account["id"], staff_id,
                        document["title"], document["instructions"],
                    )
                    if created is None:  # The account stopped being Active meanwhile.
                        raise CitizenAccountError("account_not_active")
                    document_requests.insert_event(
                        connection, created["id"], "requested", staff_id=staff_id,
                    )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    return len(documents)


def get_document_request_page(request_id, *, connection_factory=get_connection):
    """Read one request with its household and history for the staff detail page.

    Raises AssessmentQueryError("document_request_missing") if the request does not
    exist or belongs to a household outside the selected survey batch.
    """
    with _read_context(connection_factory) as (connection, context):
        request_row = document_requests.find_request(connection, request_id)
        if request_row is None:
            raise AssessmentQueryError("document_request_missing")
        account = citizen_accounts.find_by_id(connection, request_row["account_id"])
        if account["batch_id"] != context["batch_id"]:
            raise AssessmentQueryError("document_request_missing")
        page = read_household_header(connection, context, account["household_uuid"])
        events = document_requests.list_events(connection, request_id)
        files = document_requests.list_files(connection, request_id)
    page["request"] = present_request(request_row)
    page["files"] = present_files(files, request_row["status"])
    page["representative_name"] = account["representative_name"]
    page["history"] = present_request_history(events)
    return page


def lock_request_for_staff(connection, staff_id, request_id, batch_id, run_id):
    """Run the checks every staff action on a request needs, then return it locked.

    The request is read first to learn its household. Then the survey selection and
    the staff member are checked, as for every other staff action, before the row is
    held for changing. Raises CitizenAccountError with a fixed code.
    """
    found = document_requests.find_request(connection, request_id)
    if found is None:
        raise CitizenAccountError("request_missing")
    account = citizen_accounts.find_by_id(connection, found["account_id"])
    if account["batch_id"] != batch_id:
        raise CitizenAccountError("request_missing")
    check_actor_and_context(connection, staff_id, batch_id, run_id, account["household_uuid"])
    return document_requests.find_request(connection, request_id, lock=True)


def cancel_document_request(staff_id, request_id, batch_id, run_id, form, *,
                            connection_factory=get_connection):
    """Cancel an open request and record the reason, which the citizen will see.

    Accepted and cancelled requests are final and cannot be cancelled.
    """
    if any(type(value) is not int or value < 1 for value in (staff_id, request_id, batch_id, run_id)):
        raise CitizenAccountError("invalid_request")
    reason = validate_reason_form(form)
    try:
        with connection_factory() as connection:
            with connection.transaction():
                locked = lock_request_for_staff(connection, staff_id, request_id, batch_id, run_id)
                if locked["status"] not in OPEN_STATUSES:
                    raise CitizenAccountError("not_cancellable")
                document_requests.change_status(connection, request_id, "cancelled")
                document_requests.insert_event(
                    connection, request_id, "cancelled", staff_id=staff_id, note=reason,
                )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
