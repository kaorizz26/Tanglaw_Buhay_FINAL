"""Read-only age estimates, separate from historical SPISC screening.

Callers supply the selected batch's survey year and an explicit assessment date.
The survey observation must already exist at assessment time. Bounds describe
completed years of age, not verified current ages or subsidy eligibility.
No database, screening status, disability evidence or priority count is changed.
"""

from datetime import date

from services.screening.common import as_integer


SENIOR_CATEGORIES = (
    "recorded_senior", "reached_senior_age", "possible_senior_age",
)


def classify_senior_age(recorded_age, survey_year: int, assessment_date: date) -> dict:
    """Return a category and conservative age bounds from recorded A07.

    Accept nonnegative integral ages using existing screening parsing. Missing
    or invalid ages return unknown_age. Invalid context raises ValueError:
    survey_year must be an integer calendar year, assessment_date a date (not
    a datetime or string), and assessment cannot precede the survey year.
    For the survey year itself, assume the observation precedes assessment.
    """
    if type(survey_year) is not int or not 1 <= survey_year <= 9999:
        raise ValueError("survey_year must be an integer calendar year")
    if type(assessment_date) is not date:
        raise ValueError("assessment_date must be a calendar date")
    if assessment_date.year < survey_year:
        raise ValueError("assessment_date cannot precede the survey year")

    age = as_integer(recorded_age)
    if age is None:
        return {"category": "unknown_age", "minimum_age": None, "maximum_age": None}

    elapsed_years = assessment_date.year - survey_year
    # An unknown interview can fall anywhere in its year. The youngest possible
    # birth date is Dec 31 of (survey_year - age); the oldest is Jan 2 of
    # (survey_year - age - 1). This allows both interview and birthday uncertainty.
    # Jan 1 / Dec 31 tighten one endpoint; no exact dates are fabricated.
    is_year_end = (assessment_date.month, assessment_date.day) == (12, 31)
    is_year_start = (assessment_date.month, assessment_date.day) == (1, 1)
    minimum = max(age, age + elapsed_years - 1 + int(is_year_end))
    maximum = age + elapsed_years + 1 - int(is_year_start)

    if age >= 60:
        category = "recorded_senior"
    elif minimum >= 60:
        category = "reached_senior_age"
    elif maximum >= 60:
        category = "possible_senior_age"
    else:
        category = "below_senior_age"
    return {"category": category, "minimum_age": minimum, "maximum_age": maximum}


def summarize_household_senior_age(
    household: dict, members: list[dict], survey_year: int, assessment_date: date,
) -> dict:
    """Classify a single household's flat roster without mutating source rows.

    Members use source uuid, line_number and a07_age fields. Relevant roster
    lines are unique even when a member occurs more than once in the input.
    All member classifications remain available, including unknown ages; an
    empty roster explicitly retains unresolved age information.
    """
    # Validate context even for an empty roster.
    classify_senior_age(None, survey_year, assessment_date)
    household_uuid = household["uuid"]
    results = []
    for member in members:
        if member["uuid"] != household_uuid:
            raise ValueError("member uuid must match the household uuid")
        results.append({
            "uuid": member["uuid"], "line_number": member["line_number"],
            **classify_senior_age(member.get("a07_age"), survey_year, assessment_date),
        })
    return {
        "household_uuid": household_uuid,
        "survey_year": survey_year,
        "assessment_date": assessment_date,
        "members": tuple(results),
        "relevant_member_line_numbers": tuple(dict.fromkeys(
            member["line_number"] for member in results
            if member["category"] in SENIOR_CATEGORIES
        )),
        "has_unknown_age": not results or any(
            member["category"] == "unknown_age" for member in results
        ),
    }
