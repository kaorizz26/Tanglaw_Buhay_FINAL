"""Administrator-only aggregate Data Import history and CSV presentation."""

import csv
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


class ImportHistoryUnavailable(RuntimeError):
    """Aggregate import audit history could not be read safely."""


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


def export_import_history(connection_factory=get_connection):
    """Return a privacy-safe CSV containing retained aggregate import events."""
    events = _load_history_events(connection_factory)
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(HISTORY_HEADERS)
    for event in events:
        writer.writerow(_safe_csv_cell(value) for value in _history_row(event))
    return output.getvalue()
