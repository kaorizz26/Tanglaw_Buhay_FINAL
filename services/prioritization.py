"""Prepare stored prioritization summaries for the authenticated staff template."""

from services.assessment_queries import AssessmentQueryError, list_active_assessment_summaries


PROGRAM_ORDER = ("4Ps", "SLP", "SPISC", "TUPAD")
PAGE_SIZE = 25


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


def get_prioritization_page(page):
    """Return bounded stored rows and display metadata; never invoke an engine.

    Each source identity is read within the query service's existing read-only
    snapshot. Scores, ranks and findings pass through unchanged.
    """
    result = list_active_assessment_summaries(
        page=page, page_size=PAGE_SIZE, include_household_identity=True,
    )
    page_count = max(1, (result["total"] + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > page_count:
        return None
    rows = []
    for item in result["items"]:
        identity = item["identity"]
        rows.append({
            **item,
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
    }
