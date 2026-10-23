"""Historical SPISC screening: senior age triggers review; disability is context.

A07 is the member's age at the survey; age 60+ is the only screening trigger.
L15 records whether the household has a member with disability. It does not
identify that member. L16 is a provisional member disability response; senior
attribution requires that senior's joined L16=1 and household L15=1.

Disability evidence and warnings never change the age-based screening status.
These historical signals do not establish pension or subsidy eligibility.
"""

from .common import (
    all_joined_answers_are_no, as_integer, household_evidence, is_missing,
    make_warning, member_evidence, screening_result, valid_code,
)


def screen_spisc(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review age 60+; optional PWD evidence never changes the age classification."""
    evidence = []
    warnings = []
    has_unresolved_age = not members

    # 1. Household disability is context, never a senior-age trigger.
    household_disability = valid_code(household.get("l15_with_pwd_member"), (1, 2))
    if household_disability == 1:
        evidence.append(household_evidence(
            household, "l15_with_pwd_member",
            f"Household recorded a member with disability (L15=1) in {survey_year}; this does not identify a senior.",
            "household_disability", role="supporting_context",
        ))

    for member in members:
        # 2. Only a known age of 60+ adds trigger evidence.
        age = as_integer(member.get("a07_age"))
        if age is None:
            has_unresolved_age = True
        elif age >= 60:
            evidence.append(member_evidence(
                member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey.",
            ))

        # 3. Check every member to retain warnings, but attribute support only
        # to a corroborated senior. Keep support after that senior's trigger.
        disability_evidence, warning = _member_disability(
            household, member, survey_year,
            age=age, household_disability=household_disability,
        )
        if disability_evidence is not None:
            evidence.append(disability_evidence)
        if warning is not None:
            warnings.append(warning)

    # 4. A household/member contradiction requires complete joined No answers;
    # missing responses must not be interpreted as No.
    if (
        household_disability == 1
        and members
        and all_joined_answers_are_no(members, "l16_pwd")
    ):
        warnings.append(make_warning(
            "disability_household_member_conflict",
            "L15=1 conflicts with all joined L16=2 responses; no member is attributed.",
        ))

    # 5. A senior trigger takes precedence over unknown ages in other members.
    return screening_result(
        household, "SPISC", survey_year, evidence, has_unresolved_age,
        warnings=warnings,
    )


def _member_disability(
    household, member, survey_year, *, age, household_disability,
):
    """Return optional senior disability evidence and a warning, never a trigger.

    Age and the household answer are already validated by screen_spisc().
    Disability gaps do not contribute to the household's unresolved-age flag.
    """
    raw_disability_answer = member.get("l16_pwd")
    member_disability = valid_code(raw_disability_answer, (1, 2))
    has_joined_record = not is_missing(member.get("section_l_line_number"))

    if member_disability == 1:
        # A positive L16 alone cannot safely identify a senior with disability.
        if household_disability != 1 or not has_joined_record:
            warning = make_warning(
                "disability_member_conflict",
                "L16=1 lacks household L15=1 or joined-member corroboration.",
                member,
            )
            return None, warning
        if age is None:
            warning = make_warning(
                "senior_disability_unresolved",
                "Age is unknown; disability cannot be attributed to a senior.",
                member,
            )
            return None, warning
        if age < 60:
            return None, None  # Another member's disability cannot describe a senior.
        return _senior_disability_evidence(household, member, survey_year), None

    if member_disability is None and not is_missing(raw_disability_answer):
        warning = make_warning(
            "disability_invalid_member_code",
            "L16 is not valid under the provisional Yes/No interpretation.",
            member,
        )
        return None, warning

    if household_disability != 1 or age is None or age < 60:
        return None, None

    if member_disability is None or not has_joined_record:
        warning = make_warning(
            "senior_disability_unresolved",
            "Household disability is recorded, but this senior's disability response is unavailable.",
            member,
        )
        return None, warning
    return None, None


def _senior_disability_evidence(household, member, survey_year):
    """Format corroborated disability support without adding an age trigger."""
    evidence = member_evidence(
        member, ("a07_age", "l16_pwd"),
        f"This senior's joined record has provisional L16=1, corroborated by household L15=1 in {survey_year}.",
    )
    evidence.update(
        source_dataset="section_l02_to_l41",
        evidence_role="supporting_context",
        evidence_type="provisional_pilot",
        trigger_code="senior_disability",
        corroborating_household_values={
            "l15_with_pwd_member": household["l15_with_pwd_member"],
        },
    )
    return evidence
