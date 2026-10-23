"""TUPAD: recorded nonwork or irregular work for resident adults."""

from .common import (
    as_integer, member_evidence, screening_result, valid_code,
)
from .metadata import IRREGULAR_WORK


def screen_tupad(household: dict, members: list[dict], survey_year: int) -> dict:
    """Combine recorded member triggers; a valid trigger wins over other gaps."""
    evidence = []
    unresolved = not members
    for member in members:
        age = as_integer(member.get("a07_age"))
        b06 = valid_code(member.get("b06_ofi"), range(1, 8))

        # Known exclusions need no employment answers, including expected skips.
        if (age is not None and age < 18) or (b06 is not None and b06 != 7):
            continue
        if age is None or b06 is None:
            unresolved = True
            continue

        work_status = valid_code(member.get("e01_employed"), (1, 2))
        if work_status == 2:
            item = _nonwork_evidence(member, survey_year)
        elif work_status == 1:
            nature = valid_code(member.get("e11_nature_of_employment"), (1, 2, 3))
            if nature is None:
                unresolved = True
                continue
            if nature == 1:
                continue
            item = _irregular_work_evidence(member, nature, survey_year)
        else:
            unresolved = True
            continue

        # Government/GOCC worker class is also context, not an exclusion.
        if valid_code(member.get("e18_class_of_worker"), range(7)) is not None:
            item["observed_values"]["e18_class_of_worker"] = member[
                "e18_class_of_worker"
            ]
        evidence.append(item)
    return screening_result(household, "TUPAD", survey_year, evidence, unresolved)


def _nonwork_evidence(member, year):
    """Record reference-week nonwork without requiring downstream job details."""
    age = as_integer(member["a07_age"])
    evidence = member_evidence(
        member, ("a07_age", "b06_ofi", "e01_employed"),
        f"Member aged {age} recorded E01=2 (did not work during the survey "
        f"reference week) in {year}; this does not establish long-term unemployment.",
    )
    evidence.update(evidence_type="observed", trigger_code="observed_nonwork")
    return evidence


def _irregular_work_evidence(member, nature, year):
    """Retain the recorded nature of work as the reason for review."""
    evidence = member_evidence(
        member,
        ("a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment"),
        f"Member aged {as_integer(member['a07_age'])} recorded "
        f"{IRREGULAR_WORK[nature]} (E11={nature}) in {year}.",
    )
    evidence.update(evidence_type="observed", trigger_code="irregular_work")
    return evidence
