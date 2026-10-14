"""Prepare relevant CBMS evidence for display without inferring new MPI outcomes."""

from decimal import Decimal, InvalidOperation

from services.assessment_reference_labels import code_key, evidence_cell, reference_index
from services.mpi_indicator_content import INDICATORS
from services.prioritization import _source_text


def _relevant_age(raw, ages):
    """Include uncertain ages so gaps cannot disappear from the evidence view."""
    try:
        age = Decimal(str(raw).strip())
    except InvalidOperation:
        return True
    if not age.is_finite() or age < 0 or age != age.to_integral_value():
        return True
    youngest, oldest = ages
    return age >= youngest and (oldest is None or age <= oldest)


def _member_heading(member):
    responses = member["responses"]
    name = " ".join(
        part for field in ("a01_first_name", "a01_middle_name", "a01_last_name")
        if (part := _source_text(responses.get(field)))
    )
    return name or f"Member line {member['line_number']}"


def _member_evidence(member, spec, index):
    """Attach only an indicator's relevant answers and the observed member identity."""
    cells = [evidence_cell(
        "a07_age", member["responses"].get("a07_age"), "section_a_e_indiv", index,
    )]
    for field in spec.get("member_fields", ()):
        cells.append(evidence_cell(field, member["responses"].get(field), "section_a_e_indiv", index))
    for field in spec.get("health_fields", ()):
        cells.append(evidence_cell(field, member["health_responses"].get(field), "section_l02_to_l41", index))
    return {
        "name": _member_heading(member), "line_number": member["line_number"],
        "age": member["responses"].get("a07_age"),
        "cells": cells,
        "health_missing": bool(spec.get("health_fields")) and not member["health_present"],
        "notes": _source_context_notes(member, spec),
    }


def _source_context_notes(member, spec):
    """Label recorded gaps and approved source assumptions, never assign a flag."""
    responses = member["responses"]
    notes = []
    age = code_key(responses.get("a07_age"))
    age_number = int(age) if age and age.isdecimal() else None
    work = responses.get("e01_employed")
    work_missing = work is None or str(work).strip().upper() in ("", "NA")
    b06 = code_key(responses.get("b06_ofi"))
    if "e01_employed" in spec.get("member_fields", ()):
        if work_missing:
            notes.append(
                "No E01 work response is recorded. Missing does not mean a recorded No answer."
            )
        if age_number is not None and age_number < 15:
            notes.append(
                "B06 is outside its questionnaire age scope for this member; "
                "it does not gate Section E below age 15."
            )
        if (
            age_number is not None and age_number >= 15
            and b06 in ("1", "2", "3", "6") and not work_missing
        ):
            notes.append(
                "A work response is populated in a B06 category that skips Section E. "
                "This is a recorded routing conflict, not a response to overwrite."
            )
        if (
            spec is INDICATORS["Employment Proxy"] and age_number is not None
            and 18 <= age_number <= 65 and b06 in ("1", "2", "3") and work_missing
        ):
            notes.append(
                "This combination uses the approved adult assumed-working proxy for "
                "Employment only; no E01 work answer was observed."
            )
    health = member["health_responses"]
    treatment = health.get("l38_availed_medical_treatment")
    if (
        spec.get("health_fields") and code_key(health.get("l34_has_been_sick")) == "2"
        and treatment is not None and str(treatment).strip().upper() not in ("", "NA")
    ):
        notes.append(
            "Treatment is populated for a provisionally unaffected member; this is "
            "conflicting follow-up evidence under the approved illness rule."
        )
    return notes


def build_indicator_evidence(name, source, index):
    """Select fields relevant to the approved rule; never rerun or attribute a flag."""
    spec = INDICATORS[name]
    household_cells = [evidence_cell(field, source["household"].get(field), "household", index)
                       for field in spec.get("household_fields", ())]
    members = source["members"]
    selected = []
    if spec.get("member_fields"):
        selected = [
            member for member in members
            if _relevant_age(member["responses"].get("a07_age"), spec["ages"])
        ]
    elif spec.get("health_fields"):
        # L33=2 establishes the skip; unrelated member health must not be displayed.
        raw = source["household"].get("l33_member_who_has_been_sick")
        if str(raw).strip() not in ("2", "2.0"):
            selected = members
    member_rows = [_member_evidence(member, spec, index) for member in selected]
    return {
        "household_cells": household_cells,
        "members": member_rows,
        "member_based": bool(spec.get("member_fields") or spec.get("health_fields")),
        "roster_missing": not members,
        "roster_count": len(members) if spec.get("roster_count") else None,
        "limitations": spec.get("limitations", ()),
    }


def build_evidence_index(source):
    """Decode from the selected source batch's three reference tables only."""
    return reference_index(source["references"])
