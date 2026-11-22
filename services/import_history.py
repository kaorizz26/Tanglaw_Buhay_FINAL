"""Administrator-only aggregate Data Import history and CSV presentation."""

import csv
from dataclasses import dataclass
from io import StringIO

import psycopg

from db import get_connection
from repositories import import_validation_events
from services.data_loader import EXCEL_FILES


HISTORY_HEADERS = (
    "Attempt ID", "Uploaded at", "Uploaded by", "Action at", "Action by",
    "Outcome", "Description", "Same-year re-upload", "Survey year", "Batch ID",
    "Households file", "Households records",
    "Household members file", "Household members records",
    "Member health file", "Member health records", "Child death file",
    "Child death records", "Successfully imported records",
    "Duplicate or skipped records", "Rejected records",
    "Records containing validation errors", "Validation issue types",
    "Warning types",
)

HISTORY_DESCRIPTIONS = {
    "passed": "Validated files without storing a dataset.",
    "failed": "Validation found blocking issues; nothing was stored.",
    "rejected": "Rejected the upload or storage request; nothing was changed.",
    "staged": "Stored the validated files as a new inactive dataset batch.",
    "deleted": "Deleted an unused inactive batch and retained its audit history.",
}

OUTCOME_LABELS = {
    "passed": "Validation passed",
    "failed": "Validation failed",
    "rejected": "Import rejected",
    "staged": "Batch stored",
    "deleted": "Batch deleted",
}
IMPORT_HISTORY_OUTCOME_OPTIONS = tuple(OUTCOME_LABELS.items())
IMPORT_HISTORY_SORT_OPTIONS = (
    ("newest", "Newest activity"),
    ("oldest", "Oldest activity"),
    ("result", "Result A–Z"),
    ("result_desc", "Result Z–A"),
    ("year", "Survey year oldest–newest"),
    ("year_desc", "Survey year newest–oldest"),
    ("batch", "Batch lowest–highest"),
    ("batch_desc", "Batch highest–lowest"),
    ("admin", "Administrator A–Z"),
    ("admin_desc", "Administrator Z–A"),
    ("records", "Records lowest–highest"),
    ("records_desc", "Records highest–lowest"),
)
IMPORT_HISTORY_FILTER_LENGTH = 200
IMPORT_HISTORY_MAX_PAGE = 1_000_000


class ImportHistoryUnavailable(RuntimeError):
    """Aggregate import audit history could not be read safely."""


class ImportHistoryFilterError(ValueError):
    """Import history filters are malformed or unsupported."""


@dataclass(frozen=True)
class ImportHistoryFilters:
    """Validated administrator controls for aggregate import history."""

    search: str = ""
    outcome: str = ""
    survey_year: str = ""
    sort: str = "newest"
    page: int = 1

    @property
    def parameters(self):
        values = {
            "history_q": self.search,
            "history_outcome": self.outcome,
            "history_year": self.survey_year,
        }
        if self.sort != "newest":
            values["history_sort"] = self.sort
        if self.page > 1:
            values["history_page"] = self.page
        return {key: value for key, value in values.items() if value}

    @property
    def active(self):
        return bool(
            self.search or self.outcome or self.survey_year or self.sort != "newest"
        )


def parse_import_history_filters(arguments):
    """Read namespaced history filters without conflicting with the upload form."""
    values = {}
    fields = (
        ("history_q", ""), ("history_outcome", ""),
        ("history_year", ""), ("history_sort", "newest"),
        ("history_page", "1"),
    )
    for name, default in fields:
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise ImportHistoryFilterError("invalid_import_history_filters")
        values[name] = supplied[0] if supplied else default
    search = " ".join(values["history_q"].split())
    survey_year = values["history_year"]
    page_value = values["history_page"]
    if (
        len(search) > IMPORT_HISTORY_FILTER_LENGTH
        or "\x00" in search
        or values["history_outcome"] not in set(OUTCOME_LABELS) | {""}
        or values["history_sort"] not in {value for value, _ in IMPORT_HISTORY_SORT_OPTIONS}
        or not page_value.isascii()
        or not page_value.isdecimal()
        or not 1 <= int(page_value) <= IMPORT_HISTORY_MAX_PAGE
        or (
            survey_year
            and (
                not survey_year.isascii() or not survey_year.isdecimal()
                or len(survey_year) != 4 or not 1900 <= int(survey_year) <= 2100
            )
        )
    ):
        raise ImportHistoryFilterError("invalid_import_history_filters")
    return ImportHistoryFilters(
        search, values["history_outcome"], survey_year, values["history_sort"],
        int(page_value),
    )


def _safe_csv_cell(value):
    """Prevent spreadsheet formula execution in exported text fields."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _file_records(event, name):
    metadata = event.get("file_metadata") or {}
    audited = metadata.get(name) or {}
    records = audited.get("records")
    if type(records) is int and records >= 0:
        return records
    provenance = event.get("source_provenance") or {}
    records = provenance.get("files", {}).get(name, {}).get("records")
    return records if type(records) is int and records >= 0 else None


def _history_details(event):
    """Return one privacy-safe event for page and CSV presentation."""
    metadata = event.get("file_metadata") or {}
    result_details = metadata.get("_result", {})
    reason = result_details.get("code")
    records = {name: _file_records(event, name) for name in EXCEL_FILES}
    known_total = sum(value for value in records.values() if value is not None)
    all_known = all(value is not None for value in records.values())
    outcome = event["outcome"]
    imported = known_total if outcome in ("staged", "deleted") and all_known else 0
    duplicate_or_skipped = (
        known_total if reason == "duplicate_bundle" and all_known else 0
    )
    rejected = (
        known_total
        if outcome in ("failed", "rejected") and reason != "duplicate_bundle" and all_known
        else 0
    )
    error_records = (
        0 if outcome in ("passed", "staged", "deleted")
        else "Not separately counted"
    )
    uploaded_by = event.get("uploaded_by") or "Unknown"
    action_by = event.get("action_by") or uploaded_by
    uploaded_at = event["attempted_at"]
    action_at_value = event.get("deleted_at") or event["attempted_at"]
    description = (
        HISTORY_DESCRIPTIONS["deleted"]
        if outcome == "deleted"
        else result_details.get("description") or HISTORY_DESCRIPTIONS[outcome]
    )
    batch_id = event.get("batch_id")
    return {
        "id": event["id"],
        "uploaded_at": uploaded_at,
        "uploaded_by": uploaded_by,
        "action_at": action_at_value,
        "action_by": action_by,
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS[outcome],
        "description": description,
        "same_year_reupload": result_details.get("same_year_reupload") is True,
        "survey_year": event.get("survey_year"),
        "batch_id": batch_id,
        "records": records,
        "total_records": known_total if all_known else None,
        "imported": imported,
        "duplicate_or_skipped": duplicate_or_skipped,
        "rejected": rejected,
        "error_records": error_records,
        "error_count": event["error_count"],
        "warning_count": event["warning_count"],
    }


def _history_row(event):
    details = _history_details(event)
    records = details["records"]
    return (
        details["id"], details["uploaded_at"].isoformat(), details["uploaded_by"],
        details["action_at"].isoformat(), details["action_by"], details["outcome"],
        details["description"], "Yes" if details["same_year_reupload"] else "No",
        details["survey_year"], details["batch_id"],
        "household.csv", records["household"],
        "section_a_e_indiv.csv", records["section_a_e_indiv"],
        "section_l02_to_l41.csv", records["section_l02_to_l41"],
        "section_l11_to_l14.csv", records["section_l11_to_l14"],
        details["imported"], details["duplicate_or_skipped"], details["rejected"],
        details["error_records"], details["error_count"], details["warning_count"],
    )


def _load_history_events(connection_factory, *, limit=None):
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return import_validation_events.list_history(connection, limit=limit)
    except (psycopg.Error, RuntimeError, TypeError, ValueError, KeyError):
        raise ImportHistoryUnavailable("import_history_unavailable") from None


def get_import_history(*, limit=25, connection_factory=get_connection):
    """Return the latest aggregate audit events for the administrator page."""
    events = _load_history_events(connection_factory, limit=limit)
    try:
        return [_history_details(event) for event in events]
    except (TypeError, ValueError, KeyError):
        raise ImportHistoryUnavailable("import_history_unavailable") from None


def get_import_history_directory(filters, *, limit=25, connection_factory=get_connection):
    """Return one database-paginated history page and its navigation metadata."""
    if (
        not isinstance(filters, ImportHistoryFilters)
        or type(limit) is not int or not 1 <= limit <= 1000
    ):
        raise ImportHistoryFilterError("invalid_import_history_filters")
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            query_filters = {
                "search": filters.search,
                "outcome": filters.outcome,
                "survey_year": int(filters.survey_year) if filters.survey_year else None,
            }
            matching_total = import_validation_events.count_history(
                connection, **query_filters,
            )
            if filters.search or filters.outcome or filters.survey_year:
                total = import_validation_events.count_history(connection)
            else:
                total = matching_total
            years = import_validation_events.list_history_years(connection)
            total_pages = max(1, (matching_total + limit - 1) // limit)
            page = min(filters.page, total_pages)
            offset = (page - 1) * limit
            events = import_validation_events.list_history_page(
                connection,
                **query_filters,
                sort=filters.sort,
                limit=limit,
                offset=offset,
            )
        items = [_history_details(event) for event in events]
        if (
            type(matching_total) is not int or matching_total < 0
            or type(total) is not int or total < matching_total
            or not isinstance(years, list)
        ):
            raise ValueError("invalid_import_history_result")
    except (psycopg.Error, RuntimeError, TypeError, ValueError, KeyError):
        raise ImportHistoryUnavailable("import_history_unavailable") from None
    first_item = offset + 1 if items else 0
    last_item = offset + len(items) if items else 0
    return {
        "items": items,
        "page": page,
        "per_page": limit,
        "matching_total": matching_total,
        "total_pages": total_pages,
        "first_item": first_item,
        "last_item": last_item,
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "total": total,
        "years": years,
        "limit": limit,
    }


def export_import_history(connection_factory=get_connection):
    """Return a privacy-safe CSV containing retained aggregate import events."""
    events = _load_history_events(connection_factory)
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(HISTORY_HEADERS)
    for event in events:
        writer.writerow(_safe_csv_cell(value) for value in _history_row(event))
    return output.getvalue()
