"""Present stored screening results and batch evidence without running engines."""

from services.assessment_reference_labels import code_key, evidence_cell
from services.mpi_evidence import _member_heading, _relevant_age
from services.screening.metadata import PROGRAMS
from services.subsidy_screening_content import (
    FIELD_LABELS, HEALTH_FIELDS, HOUSEHOLD_FIELDS, PROGRAM_FIELDS, PROGRAM_NAMES,
    REASON_TEXT, SLP_UNRESOLVED_NOTE, STATUS_CLASSES, STATUS_EXPLANATIONS,
    STATUS_LABELS, WARNING_FIELDS, WARNING_TEXT,
)


def _missing(value):
    return value is None or str(value).strip().upper() in ("", "NA")


def _field_label(field, survey_year):
    return f"Age in {survey_year}" if field == "a07_age" else FIELD_LABELS[field]


def _answer(field, raw, index, survey_year):
    """Reuse batch decoding; never expose raw codes or reference metadata."""
    dataset = (
        "section_l02_to_l41" if field in HEALTH_FIELDS else
        "household" if field.startswith("l") else "section_a_e_indiv"
    )
    cell = evidence_cell(field, raw, dataset, index)
    meaning = cell["meaning"]
    if _missing(raw):
        value = "Not recorded"
    elif "verification" in meaning.lower() or "no verified" in meaning.lower():
        value = "Meaning needs confirmation"
    elif field == "a07_age":
        age = code_key(raw)
        value = f"{int(age)} years" if age and age.isdecimal() else "Meaning needs confirmation"
    elif field in HEALTH_FIELDS:
        value = f"{meaning.rstrip('.')} — meaning needs confirmation"
    else:
        value = meaning.rstrip(".")
    return {"label": _field_label(field, survey_year), "value": value}


def _member_name(line, members):
    member = members.get(code_key(line))
    return (
        _member_heading(member) if member
        else "Member not identified in the available roster"
    )


def _stored_reason(code, item):
    """Translate only recognized stored evidence shapes, never infer a new trigger."""
    kind = item.get("trigger_code") or item.get("pathway")
    observed = item.get("observed_values", {})
    if not kind and "a07_age" in observed and set(observed) <= {
        "a07_age", "a02_relation_to_hh_head",
    }:
        kind = {"4Ps": "child_age", "SPISC": "senior_age"}.get(code)
    return REASON_TEXT.get(
        kind, "The stored finding includes supporting information "
        "whose meaning needs confirmation.",
    )


def _source_value(source, member, field):
    if field in HOUSEHOLD_FIELDS:
        return source["household"].get(field)
    if field in HEALTH_FIELDS:
        return member["health_responses"].get(field) if member else None
    return (member["responses"] if member else source["household"]).get(field)


def _stored_items(code, finding, members):
    items = []
    for item in finding["supporting_evidence"]:
        line = item.get("member_line_number")
        items.append({
            "subject": "Household" if line is None else _member_name(line, members),
            "text": _stored_reason(code, item),
            "is_trigger": item.get("evidence_role", "trigger") == "trigger",
        })
    return items


def _record_evidence(code, finding, source, index, members):
    spec = PROGRAM_FIELDS[code]
    survey_year = source["context"]["survey_year"]
    household_cells = [
        _answer(field, source["household"].get(field), index, survey_year)
        for field in spec["household"]
    ]
    selected_lines = {
        code_key(item.get("member_line_number"))
        for item in (*finding["supporting_evidence"], *finding["data_quality_warnings"])
    }
    selected_lines.discard(None)
    evidence_fields = {}
    for item in finding["supporting_evidence"]:
        line = code_key(item.get("member_line_number"))
        if line is None:
            continue
        fields = evidence_fields.setdefault(line, set())
        fields.update(
            field for field in item.get("observed_values", {})
            if field in FIELD_LABELS and field not in HOUSEHOLD_FIELDS
        )
    for warning in finding["data_quality_warnings"]:
        line = code_key(warning.get("member_line_number"))
        if line is not None:
            evidence_fields.setdefault(line, set()).update(
                WARNING_FIELDS.get(warning.get("code"), ())
            )
    for member in source["members"]:
        age = code_key(member["responses"].get("a07_age"))
        if age is None or not age.isdecimal():
            line = code_key(member["line_number"])
            if line is not None:
                selected_lines.add(line)
                evidence_fields.setdefault(line, set()).add("a07_age")

    member_rows = []
    for member in source["members"]:
        line = code_key(member["line_number"])
        directly_related = line in selected_lines
        minimum_age = 60 if code == "SPISC" else spec["minimum_age"]
        broadly_relevant = _relevant_age(
            member["responses"].get("a07_age"), (minimum_age, None),
        )
        if finding["screening_status"] == "For Review":
            include_member = directly_related
        else:
            include_member = directly_related or broadly_relevant
        if not include_member:
            continue
        focused_fields = (
            finding["screening_status"] == "For Review" and evidence_fields.get(line)
        )
        if focused_fields:
            allowed = evidence_fields[line]
            fields = [
                field for field in (*spec["member"], *spec["health"])
                if field in allowed
            ]
        else:
            fields = list(spec["member"])
            age = member["responses"].get("a07_age")
            if code == "4Ps" and _relevant_age(age, (10, None)):
                fields += list(spec["health"])
            if code == "SPISC" and _relevant_age(age, (60, None)):
                fields += list(spec["health"])
        member_rows.append({
            "name": _member_heading(member),
            "cells": [
                _answer(field, _source_value(source, member, field), index, survey_year)
                for field in fields
            ],
        })

    differences = []
    for item in finding["supporting_evidence"]:
        line = item.get("member_line_number")
        member = members.get(code_key(line)) if line is not None else None
        observations = {
            **item.get("observed_values", {}),
            **item.get("corroborating_household_values", {}),
        }
        for field, stored in observations.items():
            if field not in FIELD_LABELS:
                continue
            recorded = _source_value(source, member, field)
            if (_missing(stored) and _missing(recorded)) or (
                not _missing(stored) and not _missing(recorded)
                and code_key(stored) == code_key(recorded)
            ):
                continue
            differences.append({
                "subject": "Household" if line is None else _member_name(line, members),
                "label": _field_label(field, survey_year),
                "stored": _answer(field, stored, index, survey_year)["value"],
                "recorded": _answer(field, recorded, index, survey_year)["value"],
            })
    return household_cells, member_rows, differences


def _information_issues(code, finding, source, members):
    issues = []
    if (
        code == "SLP" and finding["screening_status"] == "For Review"
        and SLP_UNRESOLVED_NOTE in finding["methodology_notes"]
    ):
        issues.append(
            "Some work or livelihood information remains unclear. A separate "
            "recorded circumstance still supports the For Review finding."
        )
    for warning in finding["data_quality_warnings"]:
        text = WARNING_TEXT.get(
            warning.get("code"),
            "The stored finding notes an unresolved information issue.",
        )
        line = warning.get("member_line_number")
        issues.append(
            f"{_member_name(line, members)}: {text}" if line is not None else text
        )
    if not source["members"]:
        issues.append("No household member records are available to check the finding.")
    for member in source["members"]:
        age = code_key(member["responses"].get("a07_age"))
        if age is None or not age.isdecimal():
            issues.append(f"{_member_heading(member)}: age is missing or unclear.")
    if finding["screening_status"] == "Needs Data Review" and not issues:
        issues.append(
            "The stored finding does not identify one reliable cause. Some questions "
            "may have been skipped as expected, and a missing answer does not mean No."
        )
    return list(dict.fromkeys(issues))


def prepare_subsidy_screening(source, index):
    """Keep all stored statuses and separate trigger evidence from supporting context."""
    members = {code_key(member["line_number"]): member for member in source["members"]}
    programs = []
    for code in PROGRAMS:
        finding = source["findings"]["programs"][code]
        status = finding["screening_status"]
        items = _stored_items(code, finding, members)
        household, rows, differences = _record_evidence(
            code, finding, source, index, members,
        )
        reasons = (
            [item for item in items if item["is_trigger"]]
            if status == "For Review" else []
        )
        context = [
            item for item in items
            if not item["is_trigger"] or status != "For Review"
        ]
        issues = _information_issues(code, finding, source, members)
        if status == "For Review" and not reasons:
            issues.append(
                "The stored result is For Review, but a specific supporting "
                "reason is not available in the saved evidence."
            )
        if differences:
            issues.append(
                "Some saved screening evidence differs from the available CBMS "
                "record. Both recorded values are shown below; the stored "
                "screening status is retained."
            )
        programs.append({
            "code": code, "name": PROGRAM_NAMES[code], "status": status,
            "status_label": STATUS_LABELS[status],
            "status_class": STATUS_CLASSES[status],
            "explanation": STATUS_EXPLANATIONS[status], "reasons": reasons,
            "supporting_context": context, "issues": issues,
            "household_cells": household, "members": rows, "differences": differences,
        })
    return programs
