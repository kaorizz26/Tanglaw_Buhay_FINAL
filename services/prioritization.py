"""Prepare stored prioritization summaries for the authenticated staff template."""

from dataclasses import replace

from services.assessment_queries import AssessmentQueryError, list_active_assessment_summaries
from services.assessment_filters import (
    PROGRAM_ORDER, normalize_filters,
)
from services.subsidy_priority import NO_SUBSIDY_PRIORITY, PRIMARY_PRIORITY, SECONDARY_PRIORITY


PAGE_SIZE = 25
PRIORITY_OPTIONS = (
    ("all", "All Households"), ("both", "All Priorities"),
    ("primary", PRIMARY_PRIORITY), ("secondary", SECONDARY_PRIORITY),
    ("other", "Outside Priority Groups"),
)
COMPLETENESS_OPTIONS = (
    ("all", "All assessment information"),
    ("determined", "All 16 Indicators Determined"),
    ("unknown", "Needs Assessment Attention"),
)
PRIORITY_DESCRIPTIONS = {
    "all": "Browse all assessed households, including those without priority recommendations.",
    "both": "These households have a vulnerability score of at least 20 and recorded conditions "
            "relevant to one or more assistance programs.",
    "primary": "These households have a vulnerability score above 25 and a relevant subsidy "
               "screening finding. They are recommended for consideration first.",
    "secondary": "These households have a vulnerability score from 20 to 25 and a relevant "
                 "subsidy screening finding. They are recommended for subsequent consideration.",
    "other": "These households are outside the priority groups. Staff may still examine "
             "their assessment and screening findings.",
}
PRIORITY_DISPLAY = {
    PRIMARY_PRIORITY: {
        "label": PRIMARY_PRIORITY, "style": "primary",
        "explanation": "This household is recommended for consideration first based on its "
                       "vulnerability score and subsidy screening findings.",
    },
    SECONDARY_PRIORITY: {
        "label": SECONDARY_PRIORITY, "style": "secondary",
        "explanation": "This household is recommended for subsequent consideration based on "
                       "its vulnerability score and subsidy screening findings.",
    },
    NO_SUBSIDY_PRIORITY: {
        "label": "Not in Priority Groups", "style": "other",
        "explanation": "This household is not currently in the priority groups. Staff may "
                       "still examine its screening findings.",
    },
}


def priority_display(priority):
    """Share staff wording across the household list and assessment summary."""
    return dict(PRIORITY_DISPLAY[priority])


def _household_reference_id(source_row_number):
    """Format the imported data-row position, independent of ranking or page.

    IDs are Tanglaw-Buhay references within one source batch, not PSA identifiers.
    Invalid positions fail safely rather than generating a fictional reference.
    """
    if type(source_row_number) is not int or source_row_number < 1:
        raise AssessmentQueryError("household_source_row_invalid")
    return f"HH-{source_row_number:05d}"


def _source_text(value):
    """Omit missing source text for display without rewriting stored responses."""
    if value is None or not value.strip() or value.strip() == "NA":
        return ""
    return value.strip()


def _head_name(identity):
    """Use only the uniquely identified head's observed name components."""
    if identity["head_count"] > 1:
        return "Household head needs verification"
    if identity["head_count"] == 0:
        return "Household head not recorded"
    components = (
        _source_text(identity[field])
        for field in ("first_name", "middle_name", "last_name")
    )
    return " ".join(part for part in components if part) or "Household head name not recorded"


def _page_numbers(page, page_count):
    """Keep first/last pages and a small current-page window, separated by gaps."""
    visible = sorted({
        1, page_count,
        *range(max(1, page - 1), min(page_count, page + 1) + 1),
    })
    numbers = []
    for number in visible:
        if numbers and number - numbers[-1] > 1:
            numbers.append(None)
        numbers.append(number)
    return numbers


def get_prioritization_page(page, filters=None):
    """Return bounded stored rows and display metadata; never invoke an engine.

    Each source identity is read within the query service's read-only snapshot.
    Scores and ranks retain stored values; signals include current SPISC guidance.
    """
    filters = normalize_filters(filters)
    result = list_active_assessment_summaries(
        page=page, page_size=PAGE_SIZE, include_household_identity=True,
        filters=filters, include_barangays=True, include_priority_counts=True,
    )
    page_count = max(1, (result["total"] + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > page_count:
        return None
    rows = []
    for item in result["items"]:
        identity = item["identity"]
        rows.append({
            **item,
            "priority_display": priority_display(item["subsidy_priority"]),
            "household_reference_id": _household_reference_id(identity.get("source_row_number")),
            "head_name": _head_name(identity),
            "barangay": _source_text(identity["barangay"]) or "Not recorded",
            "review_programs": [
                program for program in PROGRAM_ORDER
                if item["program_statuses"][program] == "For Review"
            ],
        })
    return {
        **result,
        "items": rows,
        "page_count": page_count,
        "page_numbers": _page_numbers(page, page_count),
        "first_record": (page - 1) * PAGE_SIZE + 1 if rows else 0,
        "last_record": (page - 1) * PAGE_SIZE + len(rows) if rows else 0,
        "filters": filters,
        "filter_parameters": filters.parameters,
        "program_options": PROGRAM_ORDER,
        "completeness_options": COMPLETENESS_OPTIONS,
        "priority_options": PRIORITY_OPTIONS,
        "priority_selectors": [
            {"value": value, "label": label, "description": description,
             "count": result["priority_counts"][value],
             "parameters": replace(filters, priority=value).parameters}
            for (value, label), description in zip(PRIORITY_OPTIONS[:4], (
                "Everyone assessed", "Households recommended for priority review",
                "Consider first", "Consider next",
            ))
        ],
        "priority_label": dict(PRIORITY_OPTIONS)[filters.priority],
        "priority_description": PRIORITY_DESCRIPTIONS[filters.priority],
        "other_filters_active": replace(filters, priority="all").active,
        "more_filters_active": bool(
            filters.programs or filters.subsidy_mode != "any"
            or filters.completeness != "all" or filters.priority == "other"
        ),
    }
