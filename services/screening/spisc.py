"""SPISC: senior-age review; corroborated disability is optional context."""

from .common import (
    all_joined_answers_are_no, as_integer, household_evidence, is_missing,
    make_warning, member_evidence, screening_result, valid_code,
)


def screen_spisc(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review age 60+; optional PWD evidence never changes the age classification."""
    evidence, warnings = [], []
    unresolved = not members
    disability_status = valid_code(household.get("l15_with_pwd_member"), (1, 2))
    if disability_status == 1:
        evidence.append(household_evidence(household, "l15_with_pwd_member",
            f"Household recorded a member with disability (L15=1) in {survey_year}; this does not identify a senior.",
            "household_disability", "supporting_context"))

    for member in members:
        age = as_integer(member.get("a07_age"))
        if age is not None and age >= 60:
            evidence.append(member_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        if age is None:
            unresolved = True

        attributed_evidence, warning = _member_disability(household, member, disability_status, survey_year)
        if attributed_evidence is not None:
            evidence.append(attributed_evidence)
        if warning is not None:
            warnings.append(warning)

    if disability_status == 1 and members and all_joined_answers_are_no(members, "l16_pwd"):
        warnings.append(make_warning("disability_household_member_conflict", "L15=1 conflicts with all joined L16=2 responses; no member is attributed."))
    return screening_result(household, "SPISC", survey_year, evidence, unresolved, warnings=warnings)


def _member_disability(household, member, disability_status, year):
    """Keep a senior's corroborated PWD support separate from the age trigger."""
    age = as_integer(member.get("a07_age"))
    raw_answer = member.get("l16_pwd")
    disability_answer = valid_code(raw_answer, (1, 2))
    joined_record_exists = not is_missing(member.get("section_l_line_number"))

    if disability_answer == 1:
        if disability_status != 1 or not joined_record_exists:
            return None, make_warning("disability_member_conflict", "L16=1 lacks household L15=1 or joined-member corroboration.", member)
        if age is None:
            return None, make_warning("senior_disability_unresolved", "Age is unknown; disability cannot be attributed to a senior.", member)
        if age < 60:
            return None, None  # Another member's disability cannot describe a senior.
        evidence = member_evidence(member, ("a07_age", "l16_pwd"),
            f"This senior's joined record has provisional L16=1, corroborated by household L15=1 in {year}.")
        evidence.update(
            source_dataset="section_l02_to_l41", evidence_role="supporting_context",
            evidence_type="provisional_pilot", trigger_code="senior_disability",
            corroborating_household_values={"l15_with_pwd_member": household["l15_with_pwd_member"]},
        )
        return evidence, None

    if disability_answer is None and not is_missing(raw_answer):
        return None, make_warning("disability_invalid_member_code", "L16 is not valid under the provisional Yes/No interpretation.", member)
    senior_response_missing = disability_answer is None or not joined_record_exists
    if disability_status == 1 and age is not None and age >= 60 and senior_response_missing:
        return None, make_warning("senior_disability_unresolved", "Household disability is recorded, but this senior's disability response is unavailable.", member)
    return None, None
