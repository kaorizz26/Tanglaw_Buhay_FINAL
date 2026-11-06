"""Staff review of uploaded documents: download a file, accept it, or ask for a correction.

Only LGU Staff reach these functions. Each decision runs in one transaction that holds
the request, so two staff members (or a cancellation) cannot clash, and it is recorded
against the exact file that was reviewed. Accepting a document is only a decision about
the document: it never changes assessments, prioritization or Staff Reviews.
"""

import psycopg

from db import get_connection
from repositories import citizen_accounts, document_requests
from services import document_storage as storage
from services.assessment_queries import AssessmentQueryError, _read_context
from services.citizen_accounts import CitizenAccountError
from services.document_requests import lock_request_for_staff, validate_reason_form

REVIEWABLE_STATUS = "submitted"


def _check_ids(*values):
    """Reject malformed identifiers before any database work."""
    if any(type(value) is not int or not 0 < value < 2**63 for value in values):
        raise CitizenAccountError("invalid_request")


def _record_decision(staff_id, request_id, batch_id, run_id, file_id, *,
                     new_status, action, note, connection_factory):
    """Move a Ready for Review request to its new status and record who decided and why.

    The file the staff member looked at must still be the newest one, so an older
    file can never be accepted by mistake. Raises CitizenAccountError with a fixed
    code: not_reviewable, file_changed, request_missing, unauthorized and so on.
    """
    try:
        with connection_factory() as connection:
            with connection.transaction():
                locked = lock_request_for_staff(connection, staff_id, request_id, batch_id, run_id)
                if locked["status"] != REVIEWABLE_STATUS:
                    raise CitizenAccountError("not_reviewable")
                files = document_requests.list_files(connection, request_id)
                if not files:
                    raise CitizenAccountError("not_reviewable")
                if files[0]["id"] != file_id:
                    raise CitizenAccountError("file_changed")
                document_requests.change_status(connection, request_id, new_status)
                document_requests.insert_event(
                    connection, request_id, action, staff_id=staff_id, file_id=file_id,
                    note=note,
                )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None


def accept_document(staff_id, request_id, batch_id, run_id, file_id, *,
                    connection_factory=get_connection):
    """Accept the newest uploaded file. Accepted is final."""
    _check_ids(staff_id, request_id, batch_id, run_id, file_id)
    _record_decision(
        staff_id, request_id, batch_id, run_id, file_id, new_status="accepted",
        action="accepted", note=None, connection_factory=connection_factory,
    )


def request_correction(staff_id, request_id, batch_id, run_id, file_id, form, *,
                       connection_factory=get_connection):
    """Ask for a corrected file; the reason is written for the representative to read."""
    _check_ids(staff_id, request_id, batch_id, run_id, file_id)
    reason = validate_reason_form(form)
    _record_decision(
        staff_id, request_id, batch_id, run_id, file_id, new_status="needs_correction",
        action="correction_requested", note=reason, connection_factory=connection_factory,
    )


def open_file_for_staff(staff_id, request_id, file_id, *, connection_factory=get_connection):
    """Return the path, display name and content type of an uploaded file for download.

    The staff member must still be active LGU Staff, the file must belong to the given
    request, and that request's household must be in the selected survey. Every
    failure gives the same "request_missing" answer, so nothing is revealed about
    files that exist elsewhere.
    """
    _check_ids(staff_id, request_id, file_id)
    try:
        with connection_factory() as connection:  # Holding the staff row needs a normal transaction.
            if not citizen_accounts.active_lgu_staff_exists(connection, staff_id):
                raise CitizenAccountError("unauthorized")
        with _read_context(connection_factory) as (connection, context):
            record = document_requests.find_file(connection, file_id)
            request_row = document_requests.find_request(connection, request_id)
            account = (citizen_accounts.find_by_id(connection, request_row["account_id"])
                       if request_row else None)
        # Checked after the read, because the read helper turns other errors into "unavailable".
        if (record is None or account is None or record["request_id"] != request_id
                or account["batch_id"] != context["batch_id"]):
            raise CitizenAccountError("request_missing")
        path = storage.stored_file_path(record["storage_key"])
    except storage.DocumentStorageError as problem:
        raise CitizenAccountError(
            "unavailable" if str(problem) == "not_configured" else "request_missing"
        ) from None
    except CitizenAccountError:
        raise
    except (AssessmentQueryError, psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    extension = record["storage_key"].rsplit(".", 1)[1]
    return {
        "path": path, "display_name": record["display_name"],
        "content_type": storage.FILE_TYPES[extension]["content_type"],
    }
