"""TUPAD: irregular work and approved observed/assumed nonwork review."""

from .common import (
    age_at_least, as_integer, is_missing, make_warning,
    member_evidence, screening_result, valid_code,
)
from .metadata import IRREGULAR_WORK


def screen_tupad(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review age 18+ with irregular work, observed nonwork or approved assumed nonwork.

    Overseas work is supporting context only. Missing E01 with populated job
    details remains contradictory, and B06=6 stays non-applicable.
    """
    evidence, warnings = [], []
    unresolved = not members
    for member in members:
        adult = age_at_least(member, 18)
        if adult is False:
            continue
        b06 = valid_code(member.get("b06_ofi"), range(1, 8))
        raw_work = member.get("e01_employed")
        work_status = valid_code(raw_work, (1, 2))
        details_present = any(not is_missing(member.get(field)) for field in
        ("e11_nature_of_employment", "e18_class_of_worker"))

        if b06 in (1, 2, 3, 6):
            if not is_missing(raw_work) or details_present:
                warnings.append(make_warning(
                    "employment_skip_conflict",
                    "Employment responses are populated despite a B06 Section E skip.", member,
                ))
                if b06 != 6:
                    unresolved = True
            elif b06 in (1, 2, 3) and adult is True:
                context = member_evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
                    f"B06={b06} with missing E01 is ASSUMED overseas employment for the {survey_year} pilot; not a nonwork trigger.")
                context.update(evidence_type="pilot_assumption", evidence_role="supporting_context",
                trigger_code="assumed_overseas_work")
                evidence.append(context)
            continue
        if b06 is None or adult is None:
            unresolved = True
            continue

        if work_status == 2:
            evidence.append(_nonwork_evidence(member, survey_year, assumed=False))
            continue  # Missing E11 cannot erase this independent trigger.
        if is_missing(raw_work):
            if details_present:
                warnings.append(make_warning("nonwork_imputation_conflict", "E01 is missing but employment details are populated; nonwork is not assumed.", member))
                unresolved = True
            else:
                evidence.append(_nonwork_evidence(member, survey_year, assumed=True))
            continue

        nature = valid_code(member.get("e11_nature_of_employment"), (1, 2, 3))
        if work_status == 1 and nature in IRREGULAR_WORK:
            evidence.append(_irregular_work_evidence(member, nature, survey_year))
        elif work_status != 1 or nature is None:
            unresolved = True
    return screening_result(household, "TUPAD", survey_year, evidence, unresolved, warnings=warnings)


def _nonwork_evidence(member, year, assumed):
    age = as_integer(member["a07_age"])
    if assumed:
        explanation = f"Member aged {age} has applicable missing E01 in {year}; ASSUMED not working under the pilot, not an observed response."
        evidence_type, trigger_code = "pilot_assumption", "assumed_nonwork"
    else:
        explanation = f"Member aged {age} recorded E01=2 (did not work in the reference week) in {year}; observed pilot review signal."
        evidence_type, trigger_code = "observed", "observed_nonwork"
    evidence = member_evidence(member, ("a07_age", "b06_ofi", "e01_employed"), explanation)
    evidence.update(evidence_type=evidence_type, trigger_code=trigger_code)
    return evidence


def _irregular_work_evidence(member, nature, year):
    evidence = member_evidence(member, ("a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment"),
        f"Member aged {as_integer(member['a07_age'])} recorded {IRREGULAR_WORK[nature]} (E11={nature}) in {year}.")
    evidence.update(evidence_type="observed", trigger_code="irregular_work")
    if valid_code(member.get("e18_class_of_worker"), range(7)) is not None:
        evidence["observed_values"]["e18_class_of_worker"] = member["e18_class_of_worker"]
    return evidence
