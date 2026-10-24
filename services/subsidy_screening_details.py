"""Present stored screening results and batch evidence without running engines."""

from services.assessment_reference_labels import code_key, evidence_cell
from services.mpi_evidence import _member_heading, _relevant_age
from services.screening.metadata import PROGRAMS
from services.slp_screening_presentation import prepare_slp_guidance, slp_unresolved_members
from services.spisc_screening_presentation import (
    philippine_assessment_date, prepare_spisc_guidance,
)
from services.tupad_screening_presentation import prepare_tupad_guidance
from services.subsidy_screening_content import (
    FIELD_LABELS, FOUR_PS_QUESTIONS, HEALTH_FIELDS, HOUSEHOLD_FIELDS, PROGRAM_FIELDS, PROGRAM_NAMES,
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


def _record_evidence(code, finding, source, index, members, spisc_guidance=None):
    if code == "SPISC":
        return (
            spisc_guidance["recorded_household"], spisc_guidance["recorded_members"],
            _record_differences(finding, source, index, members),
        )
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
    # Reuse the same applicability-aware descriptions as the main SLP panel.
    unresolved_members = {}
    if code == "SLP" and finding["screening_status"] == "Needs Data Review":
        unresolved_members = {
            item["line_number"]: item["unresolved_fields"]
            for item in slp_unresolved_members(source)
        }
    for member in source["members"]:
        line = code_key(member["line_number"])
        directly_related = line in selected_lines
        broadly_relevant = _relevant_age(
            member["responses"].get("a07_age"), (spec["minimum_age"], None),
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
        member_rows.append({
            "name": _member_heading(member),
            "cells": [
                _answer(field, _source_value(source, member, field), index, survey_year)
                for field in fields
            ],
        })
        unresolved_fields = unresolved_members.get(line)
        if unresolved_fields:
            member_rows[-1]["slp_uncertainty"] = " ".join(unresolved_fields.values())
            for field, cell in zip(fields, member_rows[-1]["cells"]):
                if field in unresolved_fields:
                    cell["slp_unavailable"] = True

    return household_cells, member_rows, _record_differences(finding, source, index, members)


def _record_differences(finding, source, index, members):
    """Compare saved evidence with projected source values without changing either."""
    survey_year = source["context"]["survey_year"]
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
    return differences


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


def _four_ps_guidance(finding, source, members, reasons, issues):
    """Explain saved evidence only; these questions never determine a new status."""
    status = finding["screening_status"]
    year = source["context"]["survey_year"]
    found = []
    if status == "For Review":
        for item in finding["supporting_evidence"]:
            if item.get("evidence_role", "trigger") != "trigger":
                continue
            observed = item.get("observed_values", {})
            kind = item.get("trigger_code")
            is_child = kind == "child_age" or (
                not kind and "a07_age" in observed
                and set(observed) <= {"a07_age", "a02_relation_to_hh_head"}
            )
            age = code_key(observed.get("a07_age"))
            if is_child and age and age.isdecimal() and 0 <= int(age) <= 18:
                found.append({
                    "subject": _member_name(item.get("member_line_number"), members),
                    "text": f"Age {int(age)} at the {year} CBMS survey.",
                })
            else:
                # Household-only pregnancy evidence must not acquire a member identity.
                line = item.get("member_line_number")
                found.append({
                    "subject": "Household" if line is None else _member_name(line, members),
                    "text": _stored_reason("4Ps", item),
                })
        if not found:
            found = [{"subject": "Saved finding", "text": issues[0]}]
        heading = "Why this was flagged"
        explanation = (
            "4Ps considers families with children aged 0–18 or a pregnant member. "
            "The recorded condition supports further review but does not establish eligibility."
            if reasons else
            "The saved result calls for review, but its specific supporting condition needs confirmation."
        )
        action = "Start a 4Ps Staff Review to verify current family circumstances and document findings."
    elif status == "Needs Data Review":
        found = [{"subject": "Information to clarify", "text": issue} for issue in issues]
        codes = {warning.get("code", "") for warning in finding["data_quality_warnings"]}
        pregnancy_issue = any(code.startswith("pregnancy_") for code in codes)
        heading = "Why more information is needed"
        explanation = (
            "The recorded pregnancy answers are unclear or conflicting. "
            "No other supported 4Ps screening condition was found."
            if pregnancy_issue else
            "The saved finding needs clarification before a supported 4Ps screening condition can be established."
        )
        action = "Staff may start a 4Ps Review to clarify the information and document current findings."
    else:
        found = [{
            "subject": f"CBMS {year}",
            "text": f"No child aged 0–18 or supported pregnancy condition was identified in the {year} CBMS records.",
        }]
        heading = "Why there is no review signal"
        explanation = "The available historical information did not match the preliminary 4Ps screening conditions."
        action = (
            "No review is suggested based on the historical findings. "
            "Staff may still start a review if they have a documented reason."
        )
    questions = list(FOUR_PS_QUESTIONS[status])
    if status == "Needs Data Review":
        has_age_issue = any("age is missing or unclear" in issue for issue in issues)
        topic = "pregnancy and age" if pregnancy_issue and has_age_issue else (
            "pregnancy" if pregnancy_issue else "age" if has_age_issue else "recorded"
        )
        questions.insert(0, f"Can the unclear {topic} information be confirmed?")
    return {"found": found, "why_heading": heading, "why": explanation,
            "questions": questions, "action": action}


def prepare_subsidy_screening(source, index, *, assessment_date=None):
    """Keep all stored statuses and separate trigger evidence from supporting context."""
    if assessment_date is None:
        assessment_date = philippine_assessment_date()
    members = {code_key(member["line_number"]): member for member in source["members"]}
    programs = []
    for code in PROGRAMS:
        finding = source["findings"]["programs"][code]
        status = finding["screening_status"]
        spisc_guidance = prepare_spisc_guidance(source, assessment_date, index) if code == "SPISC" else None
        items = _stored_items(code, finding, members)
        household, rows, differences = _record_evidence(
            code, finding, source, index, members, spisc_guidance,
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
        if code == "4Ps":
            programs[-1]["guidance"] = _four_ps_guidance(
                finding, source, members, reasons, issues,
            )
        elif code == "SLP":
            programs[-1]["guidance"] = prepare_slp_guidance(finding, source, index, issues)
        elif code == "SPISC":
            programs[-1]["guidance"] = spisc_guidance
        elif code == "TUPAD":
            programs[-1]["guidance"] = prepare_tupad_guidance(finding, source, issues)
    return programs
