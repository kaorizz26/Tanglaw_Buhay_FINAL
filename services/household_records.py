"""Present selected-batch household source records without changing them."""

from dataclasses import dataclass

from db import get_connection
from repositories import assessment_evidence, dataset_batches, household_records
from services.assessment_evidence_view import FIELD_LABELS, _display_value
from services.assessment_queries import AssessmentQueryError, _read_context
from services.assessment_reference_labels import evidence_cell, reference_index
from services.prioritization import _head_name, _household_reference_id, _source_text


PAGE_SIZE = 25
MAX_FILTER_LENGTH = 200
HOUSEHOLD_GROUPS = (
    ("Water and sanitation", (
        "q01_main_water", "q14_toilet_facility", "q19_share_toilet",
    )),
    ("Home and utilities", (
        "r10_tenure", "r03_roof", "r04_outer_walls", "r06_floor",
        "r07_floor_area", "r12_electricity",
    )),
)
MEMBER_DETAILS = (
    ("Relationship to household head", "a02_relation_to_hh_head"),
    ("Sex", "a05_sex"),
    ("Age at survey", "a07_age"),
    ("School attendance", "c03_currently_attending_school"),
    ("Work in the past week", "e01_employed"),
)


class InvalidRecordFilters(ValueError):
    """Reject malformed directory requests without echoing a search term."""


@dataclass(frozen=True)
class RecordFilters:
    search: str = ""
    barangay: str = ""

    @property
    def parameters(self):
        values = {}
        if self.search:
            values["q"] = self.search
        if self.barangay:
            values["barangay"] = self.barangay
        return values


def parse_record_arguments(arguments):
    values = {}
    for name in ("q", "barangay", "page"):
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise InvalidRecordFilters("invalid_record_filters")
        values[name] = supplied[0] if supplied else ""
    search = " ".join(values["q"].split())
    barangay = values["barangay"].strip()
    page = values["page"] or "1"
    if (
        len(search) > MAX_FILTER_LENGTH or "\x00" in search
        or len(barangay) > MAX_FILTER_LENGTH or "\x00" in barangay
        or not page.isascii() or not page.isdecimal()
        or len(page) > 9 or int(page) < 1
    ):
        raise InvalidRecordFilters("invalid_record_filters")
    return int(page), RecordFilters(search=search, barangay=barangay)


def get_record_page(page, filters, *, connection_factory=get_connection):
    """Return source summaries and barangays from one stable selected-run snapshot."""
    if type(page) is not int or page < 1 or not isinstance(filters, RecordFilters):
        raise InvalidRecordFilters("invalid_record_filters")
    with _read_context(connection_factory) as (connection, context):
        barangays = dataset_batches.list_barangays(connection, context["batch_id"])
        if filters.barangay and filters.barangay not in barangays:
            raise InvalidRecordFilters("invalid_record_filters")
        total = household_records.count_records(
            connection, context["batch_id"], context["run_id"],
            search=filters.search, barangay=filters.barangay,
        )
        if not filters.parameters and total != context["household_count"]:
            raise AssessmentQueryError("household_count_mismatch")
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > page_count:
            return None
        rows = household_records.list_records(
            connection, context["batch_id"], context["run_id"],
            search=filters.search, barangay=filters.barangay,
            limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE,
        )
        return {
            "context": context, "filters": filters, "barangays": barangays,
            "total": total, "page": page, "page_count": page_count,
            "range_start": (page - 1) * PAGE_SIZE + 1 if rows else 0,
            "range_end": (page - 1) * PAGE_SIZE + len(rows),
            "items": [{
                "household_uuid": row["household_uuid"],
                "reference_id": _household_reference_id(row["source_row_number"]),
                "head_name": _head_name(row),
                "barangay": _source_text(row["barangay"]) or "Not recorded",
                "member_count": row["member_count"],
            } for row in rows],
        }


def _recorded_cell(field, raw, dataset, index, *, label=None):
    cell = evidence_cell(field, raw, dataset, index)
    value = _display_value(cell)
    if value == "Not recorded" and field in (
        "c03_currently_attending_school", "e01_employed",
    ):
        value = "Not recorded or skipped"
    return {"label": label or FIELD_LABELS.get(field, cell["label"]), "value": value}


def get_record_detail(household_uuid, *, connection_factory=get_connection):
    """Show selected, original household and roster answers in plain language."""
    if not isinstance(household_uuid, str) or not household_uuid.strip():
        raise AssessmentQueryError("invalid_household_uuid")
    with _read_context(connection_factory) as (connection, context):
        record = household_records.find_record(
            connection, context["batch_id"], context["run_id"], household_uuid,
        )
        if record is None:
            raise AssessmentQueryError("household_record_missing")
        identities = dataset_batches.find_household_identities(
            connection, context["batch_id"], [household_uuid],
        )
        if len(identities) != 1:
            raise AssessmentQueryError("household_identity_missing")
        members = household_records.list_record_members(
            connection, context["batch_id"], household_uuid,
        )
        references = assessment_evidence.list_evidence_references(
            connection, context["batch_id"],
        )
        index = reference_index(references)
        return {
            "context": context, "household_uuid": household_uuid,
            "reference_id": _household_reference_id(record["source_row_number"]),
            "head_name": _head_name(identities[0]),
            "barangay": _source_text(record["barangay"]) or "Not recorded",
            "member_count": len(members),
            "groups": [{
                "title": title,
                "cells": [
                    _recorded_cell(field, record["responses"].get(field),
                                   "household", index)
                    for field in fields
                ],
            } for title, fields in HOUSEHOLD_GROUPS],
            "members": [{
                "name": " ".join(filter(None, (
                    _source_text(member["responses"].get(field))
                    for field in ("a01_first_name", "a01_middle_name", "a01_last_name")
                ))) or f"Member {member['line_number']}",
                "line_number": member["line_number"],
                "cells": [
                    _recorded_cell(field, member["responses"].get(field),
                                   "section_a_e_indiv", index, label=label)
                    for label, field in MEMBER_DETAILS
                ],
            } for member in members],
        }
