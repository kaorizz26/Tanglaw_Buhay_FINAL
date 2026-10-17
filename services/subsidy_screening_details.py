"""Present stored screening results and batch evidence without running engines."""

from services.assessment_reference_labels import code_key, evidence_cell
from services.mpi_evidence import _member_heading, _relevant_age
from services.screening.metadata import PROGRAMS
from services.subsidy_screening_content import (
    FIELD_LABELS, HEALTH_FIELDS, HOUSEHOLD_FIELDS, PROGRAM_FIELDS, PROGRAM_NAMES,
    REASON_TEXT, REQUIREMENT_TEXT, SLP_UNRESOLVED_NOTE, STATUS_EXPLANATIONS,
    WARNING_TEXT,
)


def _missing(value):
    return value is None or str(value).strip().upper() in ("", "NA")


def _answer(field, raw, index):
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
        value = meaning
    elif field in HEALTH_FIELDS:
        value = f"{meaning.rstrip('.')} — meaning needs confirmation"
    else:
        value = meaning.rstrip(".")
    return {"label": FIELD_LABELS[field], "value": value}


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
    household_cells = [
        _answer(field, source["household"].get(field), index)
        for field in spec["household"]
    ]
    selected_lines = {
        code_key(item.get("member_line_number"))
        for item in (*finding["supporting_evidence"], *finding["data_quality_warnings"])
    }
    member_rows = []
    for member in source["members"]:
        if not (
            code_key(member["line_number"]) in selected_lines
            or _relevant_age(
                member["responses"].get("a07_age"), (spec["minimum_age"], None),
            )
        ):
            continue
        fields = list(spec["member"])
        age = member["responses"].get("a07_age")
        if code == "4Ps" and _relevant_age(age, (10, None)):
            fields += list(spec["health"])
        if code == "SPISC" and _relevant_age(age, (60, None)):
            fields += list(spec["health"])
        member_rows.append({
            "name": _member_heading(member),
            "cells": [
                _answer(field, _source_value(source, member, field), index)
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
                "label": FIELD_LABELS[field],
                "stored": _answer(field, stored, index)["value"],
                "recorded": _answer(field, recorded, index)["value"],
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
            warning.get("code"), "The stored finding notes an unresolved "
            "information issue; confirm the relevant answers.",
        )
        line = warning.get("member_line_number")
        issues.append(
            f"{_member_name(line, members)}: {text}" if line is not None else text
        )
    if not source["members"]:
        issues.append("No household member records are available to check the finding.")
    if code == "4Ps" and finding["screening_status"] == "Needs Data Review":
        absent = [
            FIELD_LABELS[field].lower()
            for field in PROGRAM_FIELDS[code]["household"]
            if _missing(source["household"].get(field))
        ]
        if absent:
            issues.append(
                f"Not recorded — {', '.join(absent)}. "
                "Some questions may have been skipped; missing does not mean No."
            )
    for member in source["members"]:
        age = code_key(member["responses"].get("a07_age"))
        if age is None or not age.isdecimal():
            issues.append(f"{_member_heading(member)}: age is missing or unclear.")
        if (
            finding["screening_status"] != "Needs Data Review"
            or code not in ("SLP", "TUPAD")
        ):
            continue
        if not _relevant_age(
            member["responses"].get("a07_age"),
            (PROGRAM_FIELDS[code]["minimum_age"], None),
        ):
            continue
        absent = [
            FIELD_LABELS[field].lower() for field in (
                "b06_ofi", "e01_employed", "e18_class_of_worker",
                "e11_nature_of_employment",
            ) if _missing(member["responses"].get(field))
        ]
        if absent:
            issues.append(
                f"{_member_heading(member)}: not recorded — {', '.join(absent)}. "
                "Some questions may have been skipped; missing does not mean No."
            )
    if finding["screening_status"] == "Needs Data Review" and not issues:
        issues.append(
            "The stored finding does not identify the exact unresolved answer. "
            "Check the available household and member information."
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
                "reason is not available. Confirm the basis before making "
                "a program decision."
            )
        if differences:
            issues.append(
                "Some saved screening evidence differs from the available CBMS "
                "record. Both are shown below for confirmation; the stored "
                "screening status is retained."
            )
        programs.append({
            "code": code, "name": PROGRAM_NAMES[code], "status": status,
            "status_label": (
                "No Review Signal"
                if status == "No Historical CBMS Trigger" else status
            ),
            "status_class": {
                "For Review": "review", "Needs Data Review": "data",
            }.get(status, "none"),
            "explanation": STATUS_EXPLANATIONS[status], "reasons": reasons,
            "supporting_context": context, "issues": issues,
            "requirements": list(dict.fromkeys(
                REQUIREMENT_TEXT.get(
                    text, "Other program requirements still need confirmation.",
                )
                for text in finding["key_unverified_requirements"]
            )),
            "household_cells": household, "members": rows, "differences": differences,
        })
    return programs
