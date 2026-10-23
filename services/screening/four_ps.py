"""Historical 4Ps screening with child and pregnancy review signals.

L01 records household pregnancy history and controls pregnancy-question skips.
L06 records current household pregnancy; L06=1 triggers review unless L01=2
contradicts it. L07 is a provisional member pregnancy response: attribution
requires consistent household answers and a joined female member aged 10+.

The supporting_evidence output holds the accepted trigger records. Warnings
describe data issues; unresolved information records gaps or conflicts. Neither
is a trigger. A valid trigger takes precedence without discarding warnings.
"""

from .common import (
    all_joined_answers_are_no, as_integer,
    household_evidence, is_missing, make_warning, member_evidence,
    screening_result, valid_code,
)


def screen_4ps(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review children aged 0-18 or consistent household pregnancy (L06=1).

    L07 attribution additionally requires a joined female member age 10+.
    Conflicting pregnancy skips stay unresolved; an independent child wins.
    """
    evidence = []
    warnings = []
    has_unresolved_information = not members

    # 1. Household pregnancy: a conflicting skip must not become a trigger.
    current_pregnancy = valid_code(
        household.get("l06_with_member_currently_pregnant"), (1, 2),
    )
    pregnancy_history = valid_code(
        household.get("l01_with_member_who_has_been_pregnant"), (1, 2),
    )
    household_pregnancy_supported = (
        current_pregnancy == 1 and pregnancy_history != 2
    )

    if current_pregnancy == 1 and pregnancy_history == 2:
        warnings.append(make_warning(
            "pregnancy_routing_conflict",
            "L01=2 skips pregnancy questions but L06=1 is populated.",
        ))
        has_unresolved_information = True
    elif household_pregnancy_supported:
        evidence.append(household_evidence(
            household, "l06_with_member_currently_pregnant",
            f"Household recorded a pregnant member (L06=1) in the {survey_year} CBMS survey.",
            "household_pregnancy",
        ))

    # 2. Child-age screening is independent of all pregnancy answers.
    has_attributed_member = False
    for member in members:
        age = as_integer(member.get("a07_age"))
        if age is None:
            has_unresolved_information = True
        elif age <= 18:
            evidence.append(member_evidence(
                member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey.",
            ))

        # 3. Keep pregnancy evidence after child evidence for the same member
        # to preserve the existing output order.
        pregnancy_evidence, warning, unresolved = _member_pregnancy(
            household, member, survey_year,
            current_pregnancy=current_pregnancy,
            pregnancy_history=pregnancy_history,
        )
        if pregnancy_evidence is not None:
            evidence.append(pregnancy_evidence)
            has_attributed_member = True
        if warning is not None:
            warnings.append(warning)
        if unresolved:
            has_unresolved_information = True

    if household_pregnancy_supported and not has_attributed_member:
        warnings.append(_attribution_warning(members))

    # 4. Final result: accepted evidence wins over unresolved information;
    # warnings remain attached even when another valid trigger exists.
    return screening_result(
        household, "4Ps", survey_year, evidence, has_unresolved_information,
        warnings=warnings,
    )


def _member_pregnancy(
    household, member, survey_year, *, current_pregnancy, pregnancy_history,
):
    """Return (attribution evidence, warning, unresolved) for one joined member.

    Invalid or unsupported positive L07 answers remain unresolved regardless of
    demographics. Missing/negative L07 answers follow household skip handling.
    Household answers are already validated by screen_4ps().
    """
    raw_pregnancy_answer = member.get("l07_currently_pregnant")
    member_pregnancy = valid_code(raw_pregnancy_answer, (1, 2))
    if member_pregnancy is None and not is_missing(raw_pregnancy_answer):
        warning = make_warning(
            "pregnancy_invalid_member_code",
            "L07 is not valid under the provisional Yes/No interpretation.",
            member,
        )
        return None, warning, True

    age = as_integer(member.get("a07_age"))
    sex = valid_code(member.get("a05_sex"), (1, 2))
    if member_pregnancy == 1:
        household_corroborates = (
            current_pregnancy == 1 and pregnancy_history != 2
        )
        is_female_age_ten_or_older = (
            sex == 2 and age is not None and age >= 10
        )
        has_joined_record = not is_missing(member.get("section_l_line_number"))
        can_attribute_pregnancy = (
            household_corroborates
            and is_female_age_ten_or_older
            and has_joined_record
        )
        # L07 alone cannot establish which member was pregnant.
        if not can_attribute_pregnancy:
            warning = make_warning(
                "pregnancy_member_conflict",
                "L07=1 lacks consistent household, female age 10+, "
                "or joined-member corroboration.",
                member,
            )
            return None, warning, True

        evidence = _member_pregnancy_evidence(household, member, age, survey_year)
        return evidence, None, False

    # Known household answers or inapplicable demographics resolve the pathway.
    if current_pregnancy is not None or pregnancy_history == 2:
        return None, None, False
    if sex == 1 or (age is not None and age < 10):
        return None, None, False
    return None, None, True


def _member_pregnancy_evidence(household, member, age, survey_year):
    """Format an already validated attribution; do not decide applicability."""
    evidence = member_evidence(
        member, ("a07_age", "a05_sex", "l07_currently_pregnant"),
        f"Joined female member aged {age} has provisional L07=1, corroborated by L06=1 in {survey_year}.",
    )
    evidence.update(
        source_dataset="section_l02_to_l41",
        evidence_type="provisional_pilot",
        trigger_code="member_pregnancy",
        corroborating_household_values={
            "l06_with_member_currently_pregnant": household[
                "l06_with_member_currently_pregnant"
            ],
        },
    )
    return evidence


def _attribution_warning(members):
    """Explain why a recorded household pregnancy lacks member attribution."""
    relevant_members = []
    demographics_complete = True
    for member in members:
        age = as_integer(member.get("a07_age"))
        sex = valid_code(member.get("a05_sex"), (1, 2))
        if sex == 2 and age is not None and age >= 10:
            relevant_members.append(member)
        if age is None or sex is None:
            demographics_complete = False

    if (
        relevant_members
        and demographics_complete
        and all_joined_answers_are_no(relevant_members, "l07_currently_pregnant")
    ):
        return make_warning(
            "pregnancy_household_member_conflict",
            "L06=1 conflicts with all relevant joined L07=2 responses; only the household signal is retained.",
        )
    return make_warning(
        "pregnancy_member_unresolved",
        "Household pregnancy is recorded, but no member can be safely attributed.",
    )
