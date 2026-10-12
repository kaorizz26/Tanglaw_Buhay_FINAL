"""Shared CBMS interpretation, evidence and result formatting. No program rules."""

from math import isfinite

import pandas as pd

from .metadata import (
    CBMS_QUESTIONNAIRE, COMMON_NOTES, PROGRAM_NOTES,
    REFERENCES, STATUSES, UNVERIFIED_REQUIREMENTS,
)


def as_integer(value: object) -> int | None:
    """Normalize a code/age for interpretation only; reject bools and fractions."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(numeric) if isfinite(numeric) and numeric >= 0 and numeric.is_integer() else None


def valid_code(value: object, allowed) -> int | None:
    code = as_integer(value)
    return code if code in allowed else None


def is_missing(value: object) -> bool:
    """Recognize null/blank/literal NA, separating absence from invalid codes."""
    return bool(pd.isna(value)) or (isinstance(value, str) and value.strip().upper() in {"", "NA"})


def make_warning(code: str, message: str, member: dict | None = None) -> dict:
    """Attach an informational data issue, never a case or verification task."""
    return {"code": code, "member_line_number": member["line_number"] if member else None,
            "message": message}


def household_evidence(household: dict, field: str, explanation: str, trigger_code: str,
                        role: str = "trigger") -> dict:
    return {"member_line_number": None, "source_dataset": "household",
            "observed_values": {field: household[field]}, "explanation": explanation,
            "trigger_code": trigger_code, "evidence_role": role, "evidence_type": "observed"}


def age_at_least(member: dict, minimum: int) -> bool | None:
    age = as_integer(member.get("a07_age"))
    return None if age is None else age >= minimum


def all_conditions(*conditions: bool | None) -> bool | None:
    """Known exclusion wins; otherwise retain unresolved applicability."""
    if any(condition is False for condition in conditions):
        return False
    return True if all(condition is True for condition in conditions) else None


def section_e_applies(member: dict) -> bool | None:
    b06 = valid_code(member.get("b06_ofi"), range(1, 8))
    return all_conditions(age_at_least(member, 5), None if b06 is None else b06 in (4, 5, 7))


def member_evidence(member: dict, fields: tuple[str, ...], explanation: str, pathway: str | None = None) -> dict:
    """Retain observed codes and the roster line, without names or inferred facts."""
    result = {
        "member_line_number": member["line_number"],
        "source_dataset": "section_a_e_indiv",
        "observed_values": {field: member[field] for field in fields},
        "explanation": explanation,
    }
    if pathway:
        result["pathway"] = pathway
    relation = valid_code(member.get("a02_relation_to_hh_head"), range(1, 27))
    if relation is not None:
        result["observed_values"]["a02_relation_to_hh_head"] = member["a02_relation_to_hh_head"]
    return result


def screening_result(household: dict, program: str, year: int, evidence: list, unresolved: bool, extra_notes=(), extra_requirements=(), warnings=()) -> dict:
    """Supported observed/pilot triggers take precedence over unrelated gaps."""
    triggers = [item for item in evidence if item.get("evidence_role", "trigger") == "trigger"]
    if triggers:
        status = STATUSES[0]
    elif unresolved:
        status = STATUSES[2]
    else:
        status = STATUSES[1]
    lines = tuple(dict.fromkeys(item["member_line_number"] for item in triggers if item["member_line_number"] is not None))
    reasons = tuple(dict.fromkeys(item["explanation"] for item in triggers))
    return {
        "household_uuid": household["uuid"], "program_code": program,
        "screening_status": status, "trigger_reasons": reasons,
        "supporting_evidence": tuple(evidence), "triggering_member_line_numbers": lines,
        "key_unverified_requirements": UNVERIFIED_REQUIREMENTS[program] + tuple(extra_requirements),
        "survey_year": year,
        "source_references": REFERENCES[program] + (CBMS_QUESTIONNAIRE,
            "data/reference/Tanglaw_Buhay_Codebook_Values_FINAL.csv",
            "data/reference/CBMS META DATA FINAL.xlsx"),
        "methodology_notes": COMMON_NOTES + PROGRAM_NOTES[program] + tuple(extra_notes),
        "data_quality_warnings": tuple(warnings),
    }


def all_joined_answers_are_no(members, field):
    """Every supplied member must have a joined, valid No response."""
    for member in members:
        if is_missing(member.get("section_l_line_number")):
            return False
        if valid_code(member.get(field), (1, 2)) != 2:
            return False
    return True
