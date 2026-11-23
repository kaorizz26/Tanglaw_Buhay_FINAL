"""Administrator review-action history, filtering, pagination and CSV export."""

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import StringIO
import re
from zoneinfo import ZoneInfo

import psycopg

from db import get_connection
from repositories import review_activity
from services.assessment_filters import PROGRAM_ORDER


PHILIPPINE_TIME = ZoneInfo("Asia/Manila")
PAGE_SIZE = 25
MAX_PAGE = 1_000_000
MAX_FILTER_LENGTH = 200
ACTION_LABELS = {
    "started": "Review started",
    "note_added": "Note added",
    "reassigned": "Responsible staff reassigned",
    "finished": "Review finished",
    "cancelled": "Review cancelled",
    "recommendation_correct": "Recommendation corrected",
    "recommendation_withdraw": "Recommendation withdrawn",
}
ACTION_OPTIONS = tuple(ACTION_LABELS.items())
SORT_OPTIONS = (
    ("newest", "Newest activity"), ("oldest", "Oldest activity"),
    ("action", "Action A–Z"), ("action_desc", "Action Z–A"),
    ("staff", "Staff A–Z"), ("staff_desc", "Staff Z–A"),
    ("household", "Household ID lowest–highest"),
    ("household_desc", "Household ID highest–lowest"),
    ("program", "Program A–Z"), ("program_desc", "Program Z–A"),
    ("year", "Survey year oldest–newest"),
    ("year_desc", "Survey year newest–oldest"),
    ("batch", "Batch lowest–highest"),
    ("batch_desc", "Batch highest–lowest"),
)
CSV_HEADERS = (
    "Action reference", "Action time (PHT)", "Action", "Staff username",
    "Household ID", "Barangay", "Program", "Review ID", "Survey year",
    "Batch ID", "Previous responsible staff", "New responsible staff",
    "Recommendation", "Description",
)


class ReviewActivityFilterError(ValueError):
    """Review-action filters are malformed or unsupported."""


class ReviewActivityUnavailable(RuntimeError):
    """Review-action history could not be read or exported safely."""


@dataclass(frozen=True)
class ReviewActivityFilters:
    search: str = ""
    action: str = ""
    program: str = ""
    survey_year: str = ""
    batch: str = ""
    date_from: str = ""
    date_to: str = ""
    sort: str = "newest"
    page: int = 1

    @property
    def household_position(self):
        match = re.fullmatch(r"HH-(\d{5,})", self.search.upper())
        return int(match.group(1)) if match else None

    @property
    def batch_id(self):
        return int(self.batch) if self.batch else None

    @property
    def started_at(self):
        return _date_boundary(self.date_from) if self.date_from else None

    @property
    def ended_before(self):
        return (_date_boundary(self.date_to) + timedelta(days=1)) if self.date_to else None

    @property
    def parameters(self):
        values = {
            "activity_q": self.search, "activity_action": self.action,
            "activity_program": self.program, "activity_year": self.survey_year,
            "activity_batch": self.batch, "activity_from": self.date_from,
            "activity_to": self.date_to,
        }
        if self.sort != "newest":
            values["activity_sort"] = self.sort
        if self.page > 1:
            values["activity_page"] = self.page
        return {key: value for key, value in values.items() if value}

    @property
    def export_parameters(self):
        return {key: value for key, value in self.parameters.items()
                if key != "activity_page"}

    @property
    def active(self):
        return bool(self.export_parameters)

    @property
    def audit_payload(self):
        return {
            "search": self.search, "action": self.action, "program": self.program,
            "survey_year": self.survey_year, "batch": self.batch,
            "date_from": self.date_from, "date_to": self.date_to, "sort": self.sort,
        }


def _date_boundary(value):
    return datetime.combine(date.fromisoformat(value), time.min, PHILIPPINE_TIME)


def parse_review_activity_filters(arguments):
    """Read namespaced action-history filters without changing queue filters."""
    fields = (
        ("activity_q", ""), ("activity_action", ""),
        ("activity_program", ""), ("activity_year", ""),
        ("activity_batch", ""), ("activity_from", ""),
        ("activity_to", ""), ("activity_sort", "newest"),
        ("activity_page", "1"),
    )
    values = {}
    for name, default in fields:
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise ReviewActivityFilterError("invalid_review_activity_filters")
        values[name] = supplied[0] if supplied else default
    search = " ".join(values["activity_q"].split())
    page = values["activity_page"]
    year = values["activity_year"]
    batch = values["activity_batch"]
    try:
        date_from = date.fromisoformat(values["activity_from"]) if values["activity_from"] else None
        date_to = date.fromisoformat(values["activity_to"]) if values["activity_to"] else None
    except ValueError as error:
        raise ReviewActivityFilterError("invalid_review_activity_filters") from error
    if (
        len(search) > MAX_FILTER_LENGTH or "\x00" in search
        or values["activity_action"] not in set(ACTION_LABELS) | {""}
        or values["activity_program"] not in set(PROGRAM_ORDER) | {""}
        or values["activity_sort"] not in {value for value, _ in SORT_OPTIONS}
        or not page.isascii() or not page.isdecimal()
        or not 1 <= int(page) <= MAX_PAGE
        or (year and (not year.isascii() or not year.isdecimal()
                      or len(year) != 4 or not 2022 <= int(year) <= 2100))
        or (batch and (not batch.isascii() or not batch.isdecimal()
                       or not 1 <= int(batch) <= 9_999_999_999))
        or (date_from and date_to and date_from > date_to)
    ):
        raise ReviewActivityFilterError("invalid_review_activity_filters")
    return ReviewActivityFilters(
        search, values["activity_action"], values["activity_program"], year, batch,
        values["activity_from"], values["activity_to"], values["activity_sort"], int(page),
    )


def _household_id(position):
    return f"HH-{position:05d}"


def _description(row):
    if row["action"] == "reassigned":
        return f"{row['previous_staff_username']} → {row['responsible_staff_username']}"
    if row["action"] == "finished":
        return f"Recommendation recorded: {row['recommendation']}"
    if row["action"] == "recommendation_correct":
        return f"Recommendation changed to: {row['recommendation']}"
    if row["action"] == "recommendation_withdraw":
        return "The recorded recommendation was withdrawn."
    return ACTION_LABELS[row["action"]] + "."


def _present(row):
    return {
        **row,
        "action_label": ACTION_LABELS[row["action"]],
        "household_reference_id": _household_id(row["source_row_number"]),
        "barangay": row["barangay"] or "Not recorded",
        "description": _description(row),
    }


def get_review_activity_page(filters, *, connection_factory=get_connection):
    """Return one administrator history page across all retained review batches."""
    if not isinstance(filters, ReviewActivityFilters):
        raise ReviewActivityFilterError("invalid_review_activity_filters")
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            matching_total = review_activity.count_actions(connection, filters)
            total_pages = max(1, (matching_total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = min(filters.page, total_pages)
            rows = review_activity.list_actions(
                connection, filters, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
            )
            years, batches = review_activity.list_filter_options(connection)
    except (psycopg.Error, TypeError, ValueError, KeyError) as error:
        raise ReviewActivityUnavailable("review_activity_unavailable") from error
    items = [_present(row) for row in rows]
    return {
        "items": items, "page": page, "per_page": PAGE_SIZE,
        "matching_total": matching_total, "total_pages": total_pages,
        "first_item": (page - 1) * PAGE_SIZE + 1 if items else 0,
        "last_item": (page - 1) * PAGE_SIZE + len(items) if items else 0,
        "has_previous": page > 1, "has_next": page < total_pages,
        "years": years, "batches": batches, "filters": filters,
        "action_options": ACTION_OPTIONS, "program_options": PROGRAM_ORDER,
        "sort_options": SORT_OPTIONS,
    }


def _safe_csv_cell(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def export_review_activity(filters, administrator_id, *, connection_factory=get_connection):
    """Export every matching metadata-only action and audit the administrator."""
    if not isinstance(filters, ReviewActivityFilters) or type(administrator_id) is not int:
        raise ReviewActivityFilterError("invalid_review_activity_export")
    try:
        with connection_factory() as connection, connection.transaction():
            rows = review_activity.list_actions(connection, filters)
            review_activity.record_export(
                connection, administrator_id, filters.audit_payload, len(rows),
            )
    except (psycopg.Error, TypeError, ValueError, KeyError) as error:
        raise ReviewActivityUnavailable("review_activity_export_unavailable") from error
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for raw in rows:
        row = _present(raw)
        values = (
            row["action_reference"],
            row["action_at"].astimezone(PHILIPPINE_TIME).strftime("%Y-%m-%d %H:%M:%S PHT"),
            row["action_label"], row["actor_username"], row["household_reference_id"],
            row["barangay"], row["program"], row["review_id"], row["survey_year"],
            row["batch_id"], row["previous_staff_username"],
            row["responsible_staff_username"], row["recommendation"], row["description"],
        )
        writer.writerow(_safe_csv_cell(value) for value in values)
    return output.getvalue()
