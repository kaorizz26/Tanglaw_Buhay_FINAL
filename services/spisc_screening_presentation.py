"""Present current-age SPISC guidance separately from saved historical findings."""

from collections import Counter
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.assessment_reference_labels import code_key
from services.mpi_evidence import _member_heading, build_evidence_index
from services.screening.common import as_integer
from services.spisc_age_progression import (
    SENIOR_CATEGORIES, age_reference_date, summarize_household_senior_age,
)
from services.spisc_disability_presentation import prepare_disability_information
from services.subsidy_screening_content import STATUS_CLASSES, STATUS_LABELS


AGE_TEXT = {
    "recorded_senior": "Recorded as a senior in the CBMS age record.",
    "reached_senior_age": "Has reached senior age since the CBMS age record. Confirm current age.",
    "possible_senior_age": "May have reached senior age. Confirm current age or date of birth.",
    "unknown_age": "Age is missing or unclear. Confirm current age or date of birth.",
}
QUESTIONS = (
    "What is the senior's current age and date of birth?",
    "Is the senior currently or previously enrolled in SPISC, or on its waiting list?",
    "Does the senior receive another pension?",
    "Does the senior have regular income or sufficient family financial support?",
    "Are there current health, mobility, or disability concerns?",
)


def philippine_assessment_date():
    """Capture one Philippine calendar date for this presentation response."""
    return datetime.now(ZoneInfo("Asia/Manila")).date()


def _batch_reference_date(context):
    """Use a verified future reference declared in existing provenance JSON.

    age_reference_date is ISO text; age_reference_date_verified must be true.
    Absent, unverified or invalid future metadata retains year-only uncertainty.
    """
    year = context["survey_year"]
    declared = context.get("source_provenance", {}).get("declared", {})
    if declared.get("age_reference_date_verified") is True:
        try:
            supplied = date.fromisoformat(declared.get("age_reference_date", ""))
            return age_reference_date(year, supplied)
        except (TypeError, ValueError):
            pass
    return age_reference_date(year)


def _age_review_reason(members, year):
    """Explain only the age categories present, in one household-level sentence."""
    counts = Counter(member["category"] for member in members)
    reasons = []
    for category in SENIOR_CATEGORIES:
        count = counts[category]
        if not count:
            continue
        subject = "one member" if count == 1 else f"{count} members"
        if category == "recorded_senior":
            verb = "was" if count == 1 else "were"
            reasons.append(f"{subject} {verb} already 60 or older in CBMS {year}")
        elif category == "reached_senior_age":
            verb = "has" if count == 1 else "have"
            reasons.append(f"{subject} {verb} reached age 60 since CBMS {year}")
        else:
            verb = "needs" if count == 1 else "need"
            reasons.append(f"{subject} may have reached age 60 and {verb} current-age confirmation")
    sentence = "; ".join(reasons)
    return sentence[0].upper() + sentence[1:] + "."


def prepare_spisc_guidance(source, assessment_date, index=None):
    """Copy scoped roster fields into SPISC-1; never run a screening engine.

    Query rows already belong to the selected household and batch. Their UUID
    is supplied by the enclosing finding; Section L joins remain in the query.
    Display status belongs only to this panel. Review requirements and priority
    services continue to receive the original saved finding.
    """
    year = source["context"]["survey_year"]
    reference = _batch_reference_date(source["context"])
    unavailable = assessment_date < (reference or date(year, 1, 1))
    household_uuid = source["findings"]["household_uuid"]
    roster = [
        {"uuid": household_uuid, "line_number": member["line_number"],
         "a07_age": member["responses"].get("a07_age")}
        for member in source["members"]
    ]
    summary = None if unavailable else summarize_household_senior_age(
        {"uuid": household_uuid}, roster, year, assessment_date,
        reference_date=reference,
    )
    classifications = summary["members"] if summary else [
        {"category": "unknown_age", "minimum_age": None, "maximum_age": None}
        for _ in roster
    ]
    disability = prepare_disability_information(
        source, index if index is not None else build_evidence_index(source),
    )
    found, recorded = [], []
    for member, classification in zip(source["members"], classifications):
        category = classification["category"]
        age = as_integer(member["responses"].get("a07_age"))
        name = _member_heading(member)
        supporting = disability["members"][code_key(member["line_number"])]
        age_cell = {
            "label": f"Age in CBMS {year}",
            "value": f"{age} years" if age is not None else "Not recorded or unclear",
            "needs_verification": age is None,
        }
        recorded.append({"name": name, "cells": [age_cell, *supporting["recorded_cells"]]})
        if category == "below_senior_age":
            continue
        found.append({
            **classification, "line_number": member["line_number"],
            "subject": name, "recorded_age": age,
            "text": "Age progression is unavailable for this assessment date."
            if unavailable else AGE_TEXT[category],
            "disability": supporting,
        })

    suggested = any(m["category"] in SENIOR_CATEGORIES for m in found)
    unknown = unavailable or not roster or summary["has_unknown_age"]
    potential = any(m["category"] == "possible_senior_age" for m in found)
    if suggested:
        display_status = "For Review"
        heading = "Why this was flagged"
        why = _age_review_reason(found, year)
        action = "Verify current circumstances and document findings."
    elif unknown:
        display_status = "Needs Data Review"
        heading = "Why more information is needed"
        why = "Age information needs confirmation before a senior-age review signal can be established."
        action = "Confirm the age information and document findings."
    else:
        display_status = "No Historical CBMS Trigger"
        heading = "Why there is no review signal"
        why = "The available age information places household members below senior age."
        action = "Staff may start a review if newer information warrants consideration."
    if unavailable:
        why = "The assessment date precedes the CBMS age reference; confirm the survey information."
    questions = list(QUESTIONS)
    if unknown or potential or not suggested:
        questions = [question.replace("senior", "member") for question in questions]
        if not suggested and not unknown:
            questions[0] = "Has any member reached age 60 or had their recorded age corrected?"
    if len(found) > 1:
        group = "members" if unknown or potential else "seniors"
        questions = [
            f"What are the current ages and dates of birth of the listed {group}?",
            f"Are any of the listed {group} currently or previously enrolled in SPISC, or on its waiting list?",
            f"Do any of the listed {group} receive another pension?",
            f"Do the listed {group} have regular income or sufficient family financial support?",
            QUESTIONS[4],
        ]
    return {
        "found": found, "age_summary": summary, "review_suggested": suggested,
        "has_unknown_age": unknown, "assessment_date": assessment_date,
        "date_label": f"{assessment_date:%b} {assessment_date.day}, {assessment_date.year}",
        "display_status_label": STATUS_LABELS[display_status],
        "display_status_class": STATUS_CLASSES[display_status],
        "why_heading": heading, "why": why, "action": action, "questions": questions,
        "household_notes": disability["notes"],
        "recorded_household": disability["household_cells"], "recorded_members": recorded,
    }
