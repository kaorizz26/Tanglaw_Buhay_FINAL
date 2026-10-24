"""Read-only age estimates, separate from historical SPISC screening.

Callers supply the selected batch's survey year and an explicit assessment date.
The survey observation must already exist at assessment time. Bounds describe
completed years of age, not verified current ages or subsidy eligibility.
No source data, stored screening status or disability evidence is changed.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from services.screening.common import as_integer


SENIOR_CATEGORIES = (
    "recorded_senior", "reached_senior_age", "possible_senior_age",
)


def philippine_assessment_date():
    """Capture one Philippine calendar date for an entire operation."""
    return datetime.now(ZoneInfo("Asia/Manila")).date()


def batch_age_reference_date(context):
    """Use the documented 2022 reference or a verified future declaration."""
    year = context["survey_year"]
    declared = context.get("source_provenance", {}).get("declared", {})
    if declared.get("age_reference_date_verified") is True:
        try:
            supplied = date.fromisoformat(declared.get("age_reference_date", ""))
            return age_reference_date(year, supplied)
        except (TypeError, ValueError):
            pass
    return age_reference_date(year)


def senior_review_status(classifications):
    """Apply the approved household policy, including an unresolved empty roster."""
    categories = [item["category"] for item in classifications]
    if any(category in SENIOR_CATEGORIES for category in categories):
        return "For Review"
    if not categories or "unknown_age" in categories:
        return "Needs Data Review"
    return "No Historical CBMS Trigger"


def current_spisc_status(ages, context, assessment_date):
    """Classify only age inputs; disability cannot create a review signal."""
    reference = batch_age_reference_date(context)
    if assessment_date < (reference or date(context["survey_year"], 1, 1)):
        return "Needs Data Review"
    return senior_review_status([
        classify_senior_age(age, context["survey_year"], assessment_date,
                            reference_date=reference)
        for age in ages
    ])


def age_reference_date(survey_year: int, supplied: date | None = None) -> date | None:
    """Use only a documented reference: PSA 2022 A07 is as of June 30.

    A caller may supply a verified reference date for another compatible batch.
    Future years have no default month/day. This never changes source answers.
    """
    if type(survey_year) is not int or not 1 <= survey_year <= 9999:
        raise ValueError("survey_year must be an integer calendar year")
    if supplied is not None:
        if type(supplied) is not date or supplied.year != survey_year:
            raise ValueError("age reference must be a date within the survey year")
        if survey_year == 2022 and supplied != date(2022, 6, 30):
            raise ValueError("2022 A07 uses the documented June 30 reference")
    return date(2022, 6, 30) if survey_year == 2022 else supplied


def _years_before(reference, years):
    """Keep Feb 29 references usable when a possible birth year is not a leap year."""
    try:
        return reference.replace(year=reference.year - years)
    except ValueError:
        return date(reference.year - years, 2, 28)


def _completed_age(birth, assessed):
    return assessed.year - birth.year - (
        (assessed.month, assessed.day) < (birth.month, birth.day)
    )


def classify_senior_age(
    recorded_age, survey_year: int, assessment_date: date, *,
    reference_date: date | None = None,
) -> dict:
    """Return a category and conservative age bounds from recorded A07.

    Accept nonnegative integral ages using existing screening parsing. Missing
    or invalid ages return unknown_age. Invalid context raises ValueError:
    survey_year must be an integer calendar year, assessment_date a date (not
    a datetime or string), and assessment cannot precede the survey year.
    A documented reference date narrows possible birth dates; otherwise use
    conservative year-only bounds. Assessment cannot precede a known reference.
    """
    reference = age_reference_date(survey_year, reference_date)
    if type(assessment_date) is not date:
        raise ValueError("assessment_date must be a calendar date")
    if assessment_date.year < survey_year:
        raise ValueError("assessment_date cannot precede the survey year")
    if reference and assessment_date < reference:
        raise ValueError("assessment_date cannot precede the age reference")

    age = as_integer(recorded_age)
    if age is None:
        return {"category": "unknown_age", "minimum_age": None, "maximum_age": None}

    if reference:
        # A completed age A means birth lies after reference minus A+1 years,
        # and on/before reference minus A years. Evaluate both possible extremes.
        try:
            oldest_birth = _years_before(reference, age + 1) + timedelta(days=1)
            youngest_birth = _years_before(reference, age)
        except (ValueError, OverflowError):
            return {"category": "unknown_age", "minimum_age": None, "maximum_age": None}
        minimum = _completed_age(youngest_birth, assessment_date)
        maximum = _completed_age(oldest_birth, assessment_date)
    else:
        minimum, maximum = _year_only_bounds(age, survey_year, assessment_date)

    if age >= 60:
        category = "recorded_senior"
    elif minimum >= 60:
        category = "reached_senior_age"
    elif maximum >= 60:
        category = "possible_senior_age"
    else:
        category = "below_senior_age"
    return {"category": category, "minimum_age": minimum, "maximum_age": maximum}


def _year_only_bounds(age, survey_year, assessment_date):
    """Retain unknown interview/birthday uncertainty for unverified future dates."""
    elapsed_years = assessment_date.year - survey_year
    # An unknown interview can fall anywhere in its year. The youngest possible
    # birth date is Dec 31 of (survey_year - age); the oldest is Jan 2 of
    # (survey_year - age - 1). This allows both interview and birthday uncertainty.
    # Jan 1 / Dec 31 tighten one endpoint; no exact dates are fabricated.
    is_year_end = (assessment_date.month, assessment_date.day) == (12, 31)
    is_year_start = (assessment_date.month, assessment_date.day) == (1, 1)
    minimum = max(age, age + elapsed_years - 1 + int(is_year_end))
    maximum = age + elapsed_years + 1 - int(is_year_start)

    return minimum, maximum


def summarize_household_senior_age(
    household: dict, members: list[dict], survey_year: int, assessment_date: date,
    *, reference_date: date | None = None,
) -> dict:
    """Classify a single household's flat roster without mutating source rows.

    Members use source uuid, line_number and a07_age fields. Relevant roster
    lines are unique even when a member occurs more than once in the input.
    All member classifications remain available, including unknown ages; an
    empty roster explicitly retains unresolved age information.
    """
    # Validate context even for an empty roster.
    classify_senior_age(None, survey_year, assessment_date, reference_date=reference_date)
    household_uuid = household["uuid"]
    results = []
    for member in members:
        if member["uuid"] != household_uuid:
            raise ValueError("member uuid must match the household uuid")
        results.append({
            "uuid": member["uuid"], "line_number": member["line_number"],
            **classify_senior_age(
                member.get("a07_age"), survey_year, assessment_date,
                reference_date=reference_date,
            ),
        })
    return {
        "household_uuid": household_uuid,
        "survey_year": survey_year,
        "assessment_date": assessment_date,
        "age_reference_date": age_reference_date(survey_year, reference_date),
        "members": tuple(results),
        "relevant_member_line_numbers": tuple(dict.fromkeys(
            member["line_number"] for member in results
            if member["category"] in SENIOR_CATEGORIES
        )),
        "has_unknown_age": not results or any(
            member["category"] == "unknown_age" for member in results
        ),
    }
