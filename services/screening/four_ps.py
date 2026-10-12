"""4Ps: child-age and historical pregnancy review, with provisional L07 support."""

from .common import (
    age_at_least, all_conditions, all_joined_answers_are_no, as_integer,
    household_evidence, is_missing, make_warning, member_evidence,
    screening_result, valid_code,
)


def screen_4ps(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review children aged 0-18 or consistent household pregnancy (L06=1).

    L07 attribution additionally requires a joined female member age 10+.
    Conflicting pregnancy skips stay unresolved; an independent child wins.
    """
    evidence, warnings = [], []
    unresolved = not members
    pregnancy_status = valid_code(household.get("l06_with_member_currently_pregnant"), (1, 2))
    ever_pregnant = valid_code(household.get("l01_with_member_who_has_been_pregnant"), (1, 2))
    routing_conflict = pregnancy_status == 1 and ever_pregnant == 2

    if routing_conflict:
        warnings.append(make_warning("pregnancy_routing_conflict", "L01=2 skips pregnancy questions but L06=1 is populated."))
        unresolved = True
    elif pregnancy_status == 1:
        evidence.append(household_evidence(household, "l06_with_member_currently_pregnant",
            f"Household recorded a pregnant member (L06=1) in the {survey_year} CBMS survey.", "household_pregnancy"))

    attributed_members = 0
    for member in members:
        age = as_integer(member.get("a07_age"))
        if age is not None and age <= 18:
            evidence.append(member_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        if age is None:
            unresolved = True

        attributed_evidence, warning, member_unresolved = _member_pregnancy(
            household, member, pregnancy_status, ever_pregnant, routing_conflict, survey_year,
        )
        if attributed_evidence is not None:
            evidence.append(attributed_evidence)
            attributed_members += 1
        if warning is not None:
            warnings.append(warning)
        if member_unresolved:
            unresolved = True

    if pregnancy_status == 1 and not routing_conflict and not attributed_members:
        warnings.append(_attribution_warning(members))
    return screening_result(household, "4Ps", survey_year, evidence, unresolved, warnings=warnings)


def _member_pregnancy(household, member, pregnancy_status, ever_pregnant, routing_conflict, year):
    """Build provisional member attribution or its warning; do not decide child age."""
    age = as_integer(member.get("a07_age"))
    sex = valid_code(member.get("a05_sex"), (1, 2))
    eligible = all_conditions(age_at_least(member, 10), None if sex is None else sex == 2)
    raw_answer = member.get("l07_currently_pregnant")
    pregnancy_answer = valid_code(raw_answer, (1, 2))
    joined_record_exists = not is_missing(member.get("section_l_line_number"))

    if pregnancy_answer == 1:
        corroborated = pregnancy_status == 1 and not routing_conflict
        if corroborated and eligible is True and joined_record_exists:
            evidence = member_evidence(member, ("a07_age", "a05_sex", "l07_currently_pregnant"),
                f"Joined female member aged {age} has provisional L07=1, corroborated by L06=1 in {year}.")
            evidence.update(
                source_dataset="section_l02_to_l41", evidence_type="provisional_pilot",
                trigger_code="member_pregnancy",
                corroborating_household_values={"l06_with_member_currently_pregnant": household["l06_with_member_currently_pregnant"]},
            )
            return evidence, None, False
        warning = make_warning("pregnancy_member_conflict", "L07=1 lacks consistent household, female age 10+, or joined-member corroboration.", member)
        return None, warning, True

    if pregnancy_answer is None and not is_missing(raw_answer):
        warning = make_warning("pregnancy_invalid_member_code", "L07 is not valid under the provisional Yes/No interpretation.", member)
        return None, warning, True

    pregnancy_is_unresolved = (
        eligible is not False and pregnancy_status != 2
        and ever_pregnant != 2 and pregnancy_status != 1
    )
    return None, None, pregnancy_is_unresolved


def _attribution_warning(members):
    """Explain why a recorded household pregnancy lacks member attribution."""
    eligible_members = []
    complete_demographics = True
    for member in members:
        age = as_integer(member.get("a07_age"))
        sex = valid_code(member.get("a05_sex"), (1, 2))
        if sex == 2 and age_at_least(member, 10) is True:
            eligible_members.append(member)
        if age is None or sex is None:
            complete_demographics = False

    all_eligible_members_said_no = all_joined_answers_are_no(eligible_members, "l07_currently_pregnant")
    if eligible_members and complete_demographics and all_eligible_members_said_no:
        return make_warning("pregnancy_household_member_conflict", "L06=1 conflicts with all relevant joined L07=2 responses; only the household signal is retained.")
    return make_warning("pregnancy_member_unresolved", "Household pregnancy is recorded, but no member can be safely attributed.")
