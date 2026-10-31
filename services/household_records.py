"""Present selected-batch household source records without changing them."""

from dataclasses import dataclass

from werkzeug.datastructures import MultiDict

from db import get_connection
from repositories import assessment_evidence, dataset_batches, household_records
from services.assessment_evidence_view import _display_value
from services.assessment_queries import AssessmentQueryError, _read_context
from services.assessment_reference_labels import evidence_cell, reference_index
from services.prioritization import _head_name, _household_reference_id, _source_text
from services.screening.common import as_integer, is_missing


PAGE_SIZE = 25
MAX_FILTER_LENGTH = 200
RECORD_RETURN_KEYS = ("record_page", "record_q", "record_barangay")
# Each section lists (staff label, source field) in display order.
HOUSEHOLD_GROUPS = (
    ("Housing & Utilities", (
        ("Building type", "r01_building_type"),
        ("House and lot arrangement", "r10_tenure"),
        ("Roof", "r03_roof"),
        ("Outer walls", "r04_outer_walls"),
        ("Floor", "r06_floor"),
        ("Floor area", "r07_floor_area"),
        ("Bedrooms", "r09_number_of_bedrooms"),
        ("Electricity", "r12_electricity"),
        ("Cooking fuel", "r15_fuel_for_cooking"),
    )),
    ("Water & Sanitation", (
        ("Main water source", "q01_main_water"),
        ("Drinking water source", "q03_drinking_water"),
        ("Toilet facility", "q14_toilet_facility"),
        ("Toilet location", "q18_toilet_facility_located"),
        ("Shared toilet", "q19_share_toilet"),
        ("Handwashing place", "q23_handwashing"),
    )),
)
# Roster table columns shown after the member number and name.
MEMBER_COLUMNS = (
    ("Relationship to head", "a02_relation_to_hh_head"),
    ("Sex", "a05_sex"),
    ("Age in 2022", "a07_age"),
    ("Highest grade completed", "c02_hgc_group"),
    ("Attending school", "c03_currently_attending_school"),
    ("Worked in the past week", "e01_employed"),
    ("Class of worker", "e18_class_of_worker"),
)
# Codebook labels are loaded only for the fields this page shows.
RECORD_REFERENCE_FIELDS = tuple(
    field for _, fields in HOUSEHOLD_GROUPS for _, field in fields
) + tuple(field for _, field in MEMBER_COLUMNS)
MAX_SURVEY_AGE = 120
# Shown in the roster when no answer was recorded; it never means "No".
NO_RECORDED_ANSWER = "—"


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


def record_return_parameters(page, filters):
    """Describe the Household Records list state as prefixed assessment-link parameters.

    The prefix keeps these values apart from the Prioritization filters, which
    use the same names (page, q, barangay) on the assessment pages.
    """
    return {
        "from_record": "1", "record_page": page,
        **{"record_" + name: value for name, value in filters.parameters.items()},
    }


def parse_record_return(arguments):
    """Read and validate the Household Records origin of an assessment page.

    Returns None when the page was not opened from Household Records, otherwise
    (page, filters). Only known parameters are accepted and no URL is ever read
    from the request, so the return link cannot point to another site.
    """
    origin_flags = arguments.getlist("from_record")
    record_keys = [key for key in arguments if key.startswith("record_")]
    if not origin_flags and not record_keys:
        return None
    if origin_flags != ["1"] or any(key not in RECORD_RETURN_KEYS for key in record_keys):
        raise InvalidRecordFilters("invalid_record_return")
    # Remove the prefix; repeated values are kept so the shared parser rejects them.
    list_arguments = MultiDict(
        (key[len("record_"):], value)
        for key in record_keys for value in arguments.getlist(key)
    )
    return parse_record_arguments(list_arguments)


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


def survey_age(raw_age):
    """Return a member's recorded 2022 age in whole years, or None if it cannot be used.

    Uses the screening engines' shared number rule (whole and non-negative) plus
    the agreed 0–120 survey-age range. Missing, fractional, text and out-of-range
    values return None so they are never placed in an age group.
    """
    age = as_integer(raw_age)
    if age is None or age > MAX_SURVEY_AGE:
        return None
    return age


def count_household_composition(members):
    """Count roster members by their recorded 2022 age group.

    Each member is counted in exactly one group, so children, adults, older
    adults and unclear ages always add up to the total number of members.
    """
    composition = {"total": len(members), "children": 0, "adults": 0,
                   "older_adults": 0, "unclear_age": 0}
    for member in members:
        age = survey_age(member["responses"].get("a07_age"))
        if age is None:
            composition["unclear_age"] += 1
        elif age < 18:
            composition["children"] += 1
        elif age < 60:
            composition["adults"] += 1
        else:
            composition["older_adults"] += 1
    return composition


def _bedroom_count(raw):
    """Show the recorded number of bedrooms, which has no codebook labels.

    The field records a count, so any whole, non-negative number is shown as
    recorded (0 is a valid answer). Other values stay visibly uncertain.
    """
    if is_missing(raw):
        return "Not recorded"
    count = as_integer(raw)
    return "Meaning unclear" if count is None else str(count)


def _household_answer(field, raw, index):
    """Decode one household answer into plain wording using the batch codebook."""
    if field == "r09_number_of_bedrooms":
        return _bedroom_count(raw)
    return _display_value(evidence_cell(field, raw, "household", index))


def _member_answer(field, raw, index):
    """Decode one roster answer for the members table.

    A dash means no answer was recorded; the question may have been skipped for
    this member, so it never stands for "No" or "Not applicable".
    """
    if is_missing(raw):
        return NO_RECORDED_ANSWER
    if field == "a07_age":
        age = survey_age(raw)
        return "Age unclear" if age is None else str(age)
    return _display_value(evidence_cell(field, raw, "section_a_e_indiv", index))


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
            connection, context["batch_id"], RECORD_REFERENCE_FIELDS,
        )
        index = reference_index(references)
        return {
            "context": context, "household_uuid": household_uuid,
            "reference_id": _household_reference_id(record["source_row_number"]),
            "head_name": _head_name(identities[0]),
            "barangay": _source_text(record["barangay"]) or "Not recorded",
            "composition": count_household_composition(members),
            "groups": [{
                "title": title,
                "cells": [
                    {"label": label,
                     "value": _household_answer(field, record["responses"].get(field), index)}
                    for label, field in fields
                ],
            } for title, fields in HOUSEHOLD_GROUPS],
            "member_columns": [label for label, _ in MEMBER_COLUMNS],
            "members": [{
                "name": " ".join(filter(None, (
                    _source_text(member["responses"].get(field))
                    for field in ("a01_first_name", "a01_middle_name", "a01_last_name")
                ))) or f"Member {member['line_number']}",
                "line_number": member["line_number"],
                "answers": [
                    _member_answer(field, member["responses"].get(field), index)
                    for _, field in MEMBER_COLUMNS
                ],
            } for member in members],
        }
