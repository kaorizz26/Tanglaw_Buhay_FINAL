"""Citizen side of the Digital Dropbox: see requested documents, upload files, download own files.

Every lookup is limited to the signed-in citizen's account in SQL. Uploading a file only
moves the request to "Waiting for Review"; it never changes assessments, subsidy
findings or Staff Reviews.
"""

import psycopg

from db import get_connection
from repositories import citizen_accounts, document_requests
from services import document_storage as storage

# Requests the citizen can upload to, and the most files one request may hold.
UPLOADABLE_STATUSES = ("requested", "needs_correction")
MAX_VERSIONS = 10
MAX_UPLOAD_MB = storage.DEFAULT_MAX_BYTES // (1024 * 1024)

STATUS_LABELS = {
    "requested": "Upload Needed", "submitted": "Waiting for Review",
    "needs_correction": "Needs Correction", "accepted": "Accepted", "cancelled": "Cancelled",
}
NEXT_STEPS = {
    "requested": f"Please upload this document. Use a PDF, JPG or PNG file up to {MAX_UPLOAD_MB} MB.",
    "submitted": "We received your file. The LGU office will review it. You do not need to do anything now.",
    "needs_correction": "Please upload a corrected file.",
    "accepted": "The LGU office accepted this document. No further action is needed.",
    "cancelled": "This request was cancelled. You do not need to upload this document.",
}
# The staff message that explains each status, when there is one.
REASON_ACTIONS = {"needs_correction": "correction_requested", "cancelled": "cancelled"}


class DocumentAccessError(Exception):
    """A document action was refused; `code` is one of the fixed codes below.

    not_found, not_open, version_limit, storage_unavailable, unavailable.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ---- Describing requests (no database access) -----------------------------------

def present_citizen_request(row, latest_file, staff_message):
    """Turn a request, its newest file and newest staff message into what the citizen sees."""
    status = row["status"]
    reason = None
    if staff_message and staff_message["action"] == REASON_ACTIONS.get(status):
        reason = staff_message["note"]
    return {
        "id": row["id"], "title": row["title"], "instructions": row["instructions"],
        "status": status, "status_label": STATUS_LABELS[status], "next_step": NEXT_STEPS[status],
        "reason": reason, "latest_file": latest_file,
        "can_upload": status in UPLOADABLE_STATUSES,
        "upload_label": "Upload Corrected File" if status == "needs_correction" else "Upload Document",
    }


def count_needing_action(requests):
    """Count the requests that are waiting for the citizen to upload something."""
    return sum(1 for item in requests if item["can_upload"])


# ---- Reading --------------------------------------------------------------------

def list_my_documents(account_id, *, connection_factory=get_connection):
    """Return the citizen's own requests, oldest first, ready to show."""
    try:
        with connection_factory() as connection:
            rows = document_requests.list_requests_for_account(connection, account_id)
            files = document_requests.list_latest_files_for_account(connection, account_id)
            messages = document_requests.list_latest_staff_messages_for_account(
                connection, account_id,
            )
    except (psycopg.Error, RuntimeError):
        raise DocumentAccessError("unavailable") from None
    files_by_request = {item["request_id"]: item for item in files}
    messages_by_request = {item["request_id"]: item for item in messages}
    return [
        present_citizen_request(
            row, files_by_request.get(row["id"]), messages_by_request.get(row["id"]),
        )
        for row in rows
    ]


def get_my_document(account_id, request_id, **options):
    """Return one of the citizen's own requests, or raise DocumentAccessError("not_found")."""
    for item in list_my_documents(account_id, **options):
        if item["id"] == request_id:
            return item
    raise DocumentAccessError("not_found")


# ---- Uploading ------------------------------------------------------------------

def _load_open_request(connection, account, request_id, *, lock):
    """Return the request if this citizen may upload to it right now, else raise.

    Checks that the account is still Active in the same sign-in session, that the
    request belongs to this account, and that its status allows an upload. With
    lock=True the account and request rows are held, so staff cannot cancel or
    deactivate between this check and the save.
    """
    current = citizen_accounts.find_by_id(connection, account["id"], lock=lock)
    if (current is None or current["status"] != "active"
            or current["session_version"] != account["session_version"]):
        raise DocumentAccessError("not_found")
    row = document_requests.find_request_for_account(
        connection, request_id, account["id"], lock=lock,
    )
    if row is None:
        raise DocumentAccessError("not_found")
    if row["status"] not in UPLOADABLE_STATUSES:
        raise DocumentAccessError("not_open")
    return row


def upload_document(account, request_id, file_name, stream, *, connection_factory=get_connection):
    """Save the citizen's file as the next version and mark the request "Waiting for Review".

    Raises DocumentUploadError for a file problem and DocumentAccessError otherwise.
    Nothing is kept if any step fails: the file is removed and the database is rolled back.
    """
    try:
        storage.storage_root()  # Fail early if private storage is not set up.
        with connection_factory() as connection:  # Quick check before touching the file.
            _load_open_request(connection, account, request_id, lock=False)
        upload = storage.validate_upload(file_name, stream)
        with storage.stored_upload(upload) as storage_key:
            with connection_factory() as connection:  # Commits when this block ends.
                with connection.transaction():
                    # Check again now that the rows are held: things may have changed.
                    _load_open_request(connection, account, request_id, lock=True)
                    version = document_requests.next_version_number(connection, request_id)
                    if version > MAX_VERSIONS:
                        raise DocumentAccessError("version_limit")
                    saved = document_requests.insert_file(
                        connection, request_id, version, upload.display_name,
                        upload.content_type, upload.size_bytes, upload.sha256, storage_key,
                    )
                    document_requests.change_status(connection, request_id, "submitted")
                    document_requests.insert_event(
                        connection, request_id, "uploaded", account_id=account["id"],
                        file_id=saved["id"],
                    )
    except storage.DocumentStorageError:
        raise DocumentAccessError("storage_unavailable") from None
    except (psycopg.Error, RuntimeError, OSError):
        raise DocumentAccessError("unavailable") from None


# ---- Downloading ----------------------------------------------------------------

def open_my_file(account_id, file_id, *, connection_factory=get_connection):
    """Return the path, display name and content type of one of the citizen's own files.

    A file that does not exist and a file that belongs to someone else give the same
    "not_found" answer. The content type comes from our own table, not the database row.
    """
    if type(file_id) is not int or not 0 < file_id < 2**63:  # Not a possible database id.
        raise DocumentAccessError("not_found")
    try:
        with connection_factory() as connection:
            record = document_requests.find_file_for_account(connection, file_id, account_id)
        if record is None:
            raise DocumentAccessError("not_found")
        path = storage.stored_file_path(record["storage_key"])
    except storage.DocumentStorageError as problem:
        raise DocumentAccessError(
            "storage_unavailable" if str(problem) == "not_configured" else "not_found"
        ) from None
    except (psycopg.Error, RuntimeError):
        raise DocumentAccessError("unavailable") from None
    extension = record["storage_key"].rsplit(".", 1)[1]
    return {
        "path": path, "display_name": record["display_name"],
        "content_type": storage.FILE_TYPES[extension]["content_type"],
    }
