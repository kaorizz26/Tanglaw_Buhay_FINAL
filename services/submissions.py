"""Staff directories for Submissions: every Citizen Account and every document request.

The directories only read. Accounts are still created and managed from the existing
Citizen Account pages, and documents are still reviewed on the request page.
"""

from dataclasses import dataclass

from werkzeug.datastructures import MultiDict

from db import get_connection
from repositories import citizen_accounts, dataset_batches, submissions
from services.assessment_queries import _read_context, _read_document_requests
from services.citizen_account_panel import STATUS_LABELS as ACCOUNT_STATUS_LABELS
from services.citizen_account_panel import present_account_panel
from services.citizen_accounts import read_household_header
from services.document_request_panel import STATUS_LABELS as REQUEST_STATUS_LABELS
from services.document_request_panel import present_document_requests
from services.prioritization import _household_reference_id, _source_text

PAGE_SIZE = 25
MAX_FILTER_LENGTH = 200
# Links from a directory to a request page carry the list state under these names,
# so the request page can offer "Back to Document Requests" with the same filters.
RETURN_PREFIX = "list_"
RETURN_KEYS = tuple(RETURN_PREFIX + name for name in ("page", "q", "status", "barangay"))
STATUS_OPTIONS = {
    "document_requests": list(REQUEST_STATUS_LABELS.items()),
    "citizen_accounts": list(ACCOUNT_STATUS_LABELS.items()),
}


class InvalidDirectoryFilters(ValueError):
    """The address asked for a search, status, barangay or page that cannot be used."""


@dataclass(frozen=True)
class DirectoryFilters:
    search: str = ""
    status: str = ""
    barangay: str = ""

    @property
    def parameters(self):
        """The filters that are set, as address parameters (empty ones are left out)."""
        values = {"q": self.search, "status": self.status, "barangay": self.barangay}
        return {name: value for name, value in values.items() if value}


def parse_directory_arguments(arguments, statuses):
    """Read page, q, status and barangay from an address; return (page, DirectoryFilters).

    `statuses` lists the status values allowed on this directory. Anything else, a
    repeated parameter or a very long text raises InvalidDirectoryFilters.
    """
    values = {}
    for name in ("q", "status", "barangay", "page"):
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise InvalidDirectoryFilters("repeated")
        values[name] = supplied[0] if supplied else ""
    search = " ".join(values["q"].split())
    barangay = values["barangay"].strip()
    status = values["status"].strip()
    page = values["page"] or "1"
    if (len(search) > MAX_FILTER_LENGTH or "\x00" in search
            or len(barangay) > MAX_FILTER_LENGTH or "\x00" in barangay
            or (status and status not in statuses)
            or not page.isascii() or not page.isdecimal() or len(page) > 9 or int(page) < 1):
        raise InvalidDirectoryFilters("invalid")
    return int(page), DirectoryFilters(search=search, status=status, barangay=barangay)


def return_parameters(page, filters):
    """Describe a directory page's state as address parameters for a link to a request."""
    return {
        "from_list": "1", RETURN_PREFIX + "page": page,
        **{RETURN_PREFIX + name: value for name, value in filters.parameters.items()},
    }


def parse_return(arguments):
    """Read the directory state from a request page's address, or None if there is none.

    Only the known parameters are read and no address is ever taken from the request,
    so the back link can only lead to our own Document Requests page.
    """
    keys = [key for key in arguments if key.startswith(RETURN_PREFIX)]
    if not arguments.getlist("from_list") and not keys:
        return None
    if arguments.getlist("from_list") != ["1"] or any(key not in RETURN_KEYS for key in keys):
        raise InvalidDirectoryFilters("invalid_return")
    plain = MultiDict(
        (key[len(RETURN_PREFIX):], value) for key in keys for value in arguments.getlist(key)
    )
    return parse_directory_arguments(plain, REQUEST_STATUS_LABELS)


def _page_window(total, page):
    """Return (page_count, offset) for a page; the first page always exists."""
    page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return page_count, (page - 1) * PAGE_SIZE


def _check_barangay(filters, barangays):
    if filters.barangay and filters.barangay not in barangays:
        raise InvalidDirectoryFilters("unknown_barangay")


def _present_row(row):
    """Fields both directories show about a household."""
    return {
        "household_id": _household_reference_id(row["source_row_number"]),
        "barangay": _source_text(row["barangay"]) or "Not recorded",
    }


def _directory(page, filters, statuses, count_rows, list_rows, present, extra=None, *,
               connection_factory):
    """Read one page of a directory from a single snapshot of the selected survey batch."""
    with _read_context(connection_factory) as (connection, context):
        batch_id = context["batch_id"]
        barangays = dataset_batches.list_barangays(connection, batch_id)
        _check_barangay(filters, barangays)
        criteria = {"search": filters.search, "status": filters.status,
                    "barangay": filters.barangay}
        total = count_rows(connection, batch_id, **criteria)
        page_count, offset = _page_window(total, page)
        if page > page_count:
            return None
        rows = list_rows(connection, batch_id, **criteria, limit=PAGE_SIZE, offset=offset)
        extras = extra(connection, batch_id) if extra else {}
    return {
        "filters": filters, "barangays": barangays, "statuses": statuses,
        "total": total, "page": page, "page_count": page_count,
        "range_start": offset + 1 if rows else 0, "range_end": offset + len(rows),
        "items": [present(row) for row in rows], **extras,
    }


def get_account_directory(page, filters, *, connection_factory=get_connection):
    """One page of Citizen Accounts, newest first; None if the page number is too high."""
    def present(row):
        return {
            **_present_row(row), "household_uuid": row["household_uuid"],
            "representative": row["representative_name"], "status": row["status"],
            "status_label": ACCOUNT_STATUS_LABELS[row["status"]],
        }

    return _directory(
        page, filters, STATUS_OPTIONS["citizen_accounts"], submissions.count_accounts,
        submissions.list_accounts, present, connection_factory=connection_factory,
    )


def get_request_directory(page, filters, *, connection_factory=get_connection):
    """One page of document requests, those waiting for review first; None if past the end."""
    def present(row):
        return {
            **_present_row(row), "id": row["id"], "title": row["title"],
            "representative": row["representative_name"], "status": row["status"],
            "status_label": REQUEST_STATUS_LABELS[row["status"]],
            "awaiting_review": row["status"] == "submitted", "updated_at": row["updated_at"],
        }

    def awaiting(connection, batch_id):
        return {"awaiting_review_count": submissions.count_awaiting_review(connection, batch_id)}

    return _directory(
        page, filters, STATUS_OPTIONS["document_requests"], submissions.count_requests,
        submissions.list_requests, present, awaiting, connection_factory=connection_factory,
    )


def get_account_detail(household_uuid, *, connection_factory=get_connection):
    """Read a household's Citizen Account and document requests for the account page.

    It reuses the Household Assessment's own readers, so both pages show the same
    account facts and the same available actions. Raises AssessmentQueryError if the
    household is not in the selected survey.
    """
    with _read_context(connection_factory) as (connection, context):
        page = read_household_header(connection, context, household_uuid)
        row = citizen_accounts.find_panel(connection, context["batch_id"], household_uuid)
        requests = _read_document_requests(connection, row)
    page["citizen_account"] = present_account_panel(row)
    page["document_requests"] = present_document_requests(requests)
    return page
