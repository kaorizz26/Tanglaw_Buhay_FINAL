"""SLP: observed nonwork (EF) and observed enterprise work (MD) pathways."""

from .common import (
    age_at_least, all_conditions, as_integer, member_evidence,
    screening_result, valid_code,
)
from .metadata import MD_CLASSES


def screen_slp(household: dict, members: list[dict], survey_year: int) -> dict:
    """Review EF at 18+ with observed nonwork, or MD at 16+ with enterprise work."""
    evidence = []
    unresolved = not members
    minor_md_participant = False
    for member in members:
        residency = valid_code(member.get("b06_ofi"), range(1, 8))
        resident_philippines = None if residency is None else residency == 7
        work_status = valid_code(member.get("e01_employed"), (1, 2))
        ef_trigger = all_conditions(
            age_at_least(member, 18), resident_philippines,
            None if work_status is None else work_status == 2,
        )
        worker_class = valid_code(member.get("e18_class_of_worker"), range(7))
        # Recorded nonwork excludes MD; downstream E18 skips are not gaps.
        md_trigger = all_conditions(
            age_at_least(member, 16), resident_philippines,
            None if work_status is None else work_status == 1,
            None if worker_class is None else worker_class in MD_CLASSES,
        )

        if ef_trigger is True:
            evidence.append(_ef_evidence(member, survey_year))
        if md_trigger is True:
            evidence.append(_md_evidence(member, survey_year))
            if as_integer(member["a07_age"]) in (16, 17):
                minor_md_participant = True
        if ef_trigger is None or md_trigger is None:
            unresolved = True

    requirements, notes = (), ()
    if minor_md_participant:
        requirements = ("Written parental/legal guardian consent for a 16-17-year-old MD participant.",)
    if evidence and unresolved:
        notes = ("One or more CBMS pathways remain unresolved; supported EF/MD evidence still warrants review.",)
    return screening_result(household, "SLP", survey_year, evidence, unresolved, notes, requirements)


def _ef_evidence(member, year):
    evidence = member_evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
        f"SLP EF: member aged {as_integer(member['a07_age'])} recorded E01=2 (did not work in the reference week) in {year}.", "EF")
    for field, label in (("c07_tvet_graduate", "TVET graduate"), ("c08_tvet_currently_attending", "attending TVET")):
        if valid_code(member.get(field), (1, 2)) == 1:
            evidence["observed_values"][field] = member[field]
            evidence["explanation"] += f" Recorded {label}."
    return evidence


def _md_evidence(member, year):
    worker_class = valid_code(member["e18_class_of_worker"], range(7))
    fields = ("a07_age", "b06_ofi", "e01_employed", "e18_class_of_worker")
    evidence = member_evidence(member, fields,
        f"SLP MD: member aged {as_integer(member['a07_age'])} recorded {MD_CLASSES[worker_class]} (E18={worker_class}) in {year}.", "MD")
    if valid_code(member.get("e11_nature_of_employment"), (1, 2, 3)) is not None:
        evidence["observed_values"]["e11_nature_of_employment"] = member["e11_nature_of_employment"]
    return evidence
