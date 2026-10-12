"""Historical four-program CBMS pre-screening, independent of MPI scoring.

These project triggers suggest staff consideration, never official eligibility,
approval, assigned cases, or required follow-up. Original values are read-only.
Age is the recorded survey age; no age/pregnancy/work condition is projected to
today. MPI E01/OFW imputations and synthetic E15 are never screening evidence.

L07 and L16 remain pending: the 2022 questionnaire/PSA metadata describe member
line numbers, but the working codebook describes Yes/No. Neither interpretation
is asserted without confirmation. Pregnancy does not generate a review flag;
verified household pregnancy skips can establish a negative pathway. SPISC uses
age alone and displays no unconfirmed person-level PWD/illness evidence.
"""

from math import isfinite
from numbers import Integral

import pandas as pd

from services.data_loader import _require_keys, join_section_l


PROGRAMS = ("4Ps", "SLP", "SPISC", "TUPAD")
STATUSES = ("For Review", "No Historical CBMS Trigger", "Needs Data Review")
CBMS_QUESTIONNAIRE = "https://psa.gov.ph/system/files/cbms/2022_Household_Profile_Questionnaire.pdf"
SLP_POLICY = "https://dswd-file-assets.s3.ap-southeast-1.amazonaws.com/wp-content/uploads/2025/07/MC-No.-03-S-2025.pdf"
TUPAD_BASE_POLICY = "https://bwsc.dole.gov.ph/wp-content/uploads/2024/04/DO-239-23-Guideline-in-the-Implementation-of-the-Department-of-Labor-and-Employment-Integrated-Livelihood-and-Emergency-Employment-Program-DILEEP.pdf"
REFERENCES = {
    "4Ps": ("https://pantawid.dswd.gov.ph/wp-content/uploads/2026/03/CCTG-ORDER-NO.04-S.2026-REVISED.pdf",),
    "SLP": (SLP_POLICY,),
    "SPISC": ("https://fo11.dswd.gov.ph/5849-2/", "https://www.dswd.gov.ph/issuance/"),
    "TUPAD": (TUPAD_BASE_POLICY, "https://dole.gov.ph/department-orders/"),
}
UNVERIFIED_REQUIREMENTS = {
    "4Ps": ("Official poverty-targeting classification.", "Current qualifying family composition.",
            "Existing beneficiary status and applicable program conditions."),
    "SLP": ("Actual livelihood or employment assistance needs.",
            "Participant suitability, preferences and official SLP assessment."),
    "SPISC": ("Current indigency and financial support.", "Existing pension receipt.",
              "Present-day circumstances and applicable SPISC conditions."),
    "TUPAD": ("Current disadvantaged/displaced worker circumstances.",
              "Current livelihood/employment situation, prior assistance and applicable DOLE conditions."),
}
COMMON_NOTES = (
    "Project screening heuristic, not official eligibility or a program score.",
    "Historical survey evidence; current circumstances are not inferred.",
    "Unverified requirements are informational; no tasks or mandatory follow-up are created.",
)
PROGRAM_NOTES = {
    "4Ps": ("CBMS household membership does not establish a single official 4Ps family unit.",
            "Pregnancy/member identification is pending L07 coding confirmation; no pregnancy flag is generated."),
    "SLP": ("EF uses recorded E01=2, not official unemployment or MPI imputation.",
            "MD uses recorded E01=1 and E18=3/4/5/6 with verified Section E applicability.",
            "E03/job-attachment routing is absent from the export; missing E11/E18 cannot be declared a verified skip solely from E01=2.",
            "MC 003 s.2025 pp.8-9 provides MD age 16+ and EF age 18+; these work-status triggers are project heuristics."),
    "SPISC": ("Age 60+ is a review signal, not proof of indigency or pension eligibility.",
              "L16/PWD coding is pending; no unconfirmed PWD or L34 illness support is displayed.",
              "The FO XI page describes older guidance; MC 022 s.2026 is listed, but its full text has not been verified."),
    "TUPAD": ("Recorded E01=1 and E11=2/3 supply historical irregular-work evidence; E01=2 alone never triggers review.",
              "Age 18+ follows DO 239-23 section 8, with no invented upper age limit.",
              "DO 239-E s.2026 is listed, but its full text has not been verified; no new exclusions are invented.",
              "Missing job-attachment routing prevents interpreting E11 absence as a verified negative solely from E01=2."),
}
MD_CLASSES = {
    3: "self-employed without paid employees",
    4: "employer in own family-operated farm/business",
    5: "paid work in own family-operated farm/business",
    6: "unpaid work in own family-operated farm/business",
}
IRREGULAR_WORK = {
    2: "short-term, seasonal or casual work",
    3: "work for different employers/customers day-to-day or week-to-week",
}
OUTPUT_COLUMNS = (
    "household_uuid", "program_code", "screening_status", "trigger_reasons",
    "supporting_evidence", "triggering_member_line_numbers",
    "key_unverified_requirements", "survey_year", "source_references", "methodology_notes",
)


def _integer(value: object) -> int | None:
    """Normalize a code/age for interpretation only; reject bools and fractions."""
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(numeric) if isfinite(numeric) and numeric >= 0 and numeric.is_integer() else None


def _code(value: object, allowed) -> int | None:
    code = _integer(value)
    return code if code in allowed else None


def _age_at_least(member: dict, minimum: int) -> bool | None:
    age = _integer(member.get("a07_age"))
    return None if age is None else age >= minimum


def _and(*conditions: bool | None) -> bool | None:
    """Known exclusion wins; otherwise retain unresolved applicability."""
    if any(condition is False for condition in conditions):
        return False
    return True if all(condition is True for condition in conditions) else None


def _e_applicable(member: dict) -> bool | None:
    b06 = _code(member.get("b06_ofi"), range(1, 8))
    return _and(_age_at_least(member, 5), None if b06 is None else b06 in (4, 5, 7))


def _observed_work_path(member: dict, field: str, target_codes, minimum_age: int) -> bool | None:
    """Require actual work and applicable Section E routing for E11/E18 triggers.

    E01=2 may mean no work with an attached job (E03), not a confirmed downstream
    skip. Since E03 is absent, retain uncertainty rather than manufacture work
    history or a negative from missing E11/E18. Out-of-age and B06 skips exclude.
    """
    applicable = _and(_age_at_least(member, minimum_age), _e_applicable(member))
    if applicable is False:
        return False
    codes = (1, 2, 3) if field == "e11_nature_of_employment" else range(7)
    value = _code(member.get(field), codes)
    if applicable is True and _code(member.get("e01_employed"), (1, 2)) == 1 and value is not None:
        return value in target_codes
    return None


def _evidence(member: dict, fields: tuple[str, ...], explanation: str, pathway: str | None = None) -> dict:
    """Retain observed codes and the roster line, without names or inferred facts."""
    result = {
        "member_line_number": member["line_number"],
        "source_dataset": "section_a_e_indiv",
        "observed_values": {field: member[field] for field in fields},
        "explanation": explanation,
    }
    if pathway:
        result["pathway"] = pathway
    relation = _code(member.get("a02_relation_to_hh_head"), range(1, 27))
    if relation is not None:
        result["observed_values"]["a02_relation_to_hh_head"] = member["a02_relation_to_hh_head"]
    return result


def _result(household: dict, program: str, year: int, evidence: list, unresolved: bool, extra_notes=(), extra_requirements=()) -> dict:
    """A confirmed trigger takes precedence over unrelated missing CBMS evidence."""
    status = STATUSES[0] if evidence else (STATUSES[2] if unresolved else STATUSES[1])
    lines = tuple(dict.fromkeys(item["member_line_number"] for item in evidence))
    reasons = tuple(dict.fromkeys(item["explanation"] for item in evidence))
    return {
        "household_uuid": household["uuid"], "program_code": program,
        "screening_status": status, "trigger_reasons": reasons,
        "supporting_evidence": tuple(evidence), "triggering_member_line_numbers": lines,
        "key_unverified_requirements": UNVERIFIED_REQUIREMENTS[program] + tuple(extra_requirements),
        "survey_year": year,
        "source_references": REFERENCES[program] + (CBMS_QUESTIONNAIRE,
            "data/reference/Tanglaw_Buhay_Codebook_Values_FINAL.csv",
            "data/reference/CBMS META DATA FINAL.xlsx"),
        "methodology_notes": COMMON_NOTES + PROGRAM_NOTES[program] + tuple(extra_notes),
    }


def _pending_pregnancy(member: dict, household: dict) -> bool | None:
    """Exclude verified demographic/household skips; never decode unconfirmed L07.

    L01=2 skips to L08; L06=2 also skips L07. A conflicting L01=2/L06=1 is
    unresolved. Absent Section L records never establish a negative themselves.
    """
    sex = _code(member.get("a05_sex"), (1, 2))
    potential = _and(_age_at_least(member, 10), None if sex is None else sex == 2)
    if potential is False:
        return False
    ever_pregnant = _code(household.get("l01_with_member_who_has_been_pregnant"), (1, 2))
    pregnant = _code(household.get("l06_with_member_currently_pregnant"), (1, 2))
    if ever_pregnant == 2 and pregnant == 1:
        return None
    if ever_pregnant == 2 or pregnant == 2:
        return False
    return None  # Member-specific pregnancy coding remains explicitly pending.


def screen_4ps(household: dict, members: list[dict], survey_year: int) -> dict:
    """Age 0-18 triggers review; the unverified pregnancy pathway stays pending."""
    evidence, unresolved = [], not members
    for member in members:
        age = _integer(member.get("a07_age"))
        if age is not None and age <= 18:
            evidence.append(_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        unresolved |= age is None or _pending_pregnancy(member, household) is None
    return _result(household, "4Ps", survey_year, evidence, unresolved)


def screen_spisc(household: dict, members: list[dict], survey_year: int) -> dict:
    """Age 60+ triggers review; unconfirmed disability support is not required."""
    evidence, unresolved = [], not members
    for member in members:
        age = _integer(member.get("a07_age"))
        if age is not None and age >= 60:
            evidence.append(_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        unresolved |= age is None
    return _result(household, "SPISC", survey_year, evidence, unresolved)


def screen_slp(household: dict, members: list[dict], survey_year: int) -> dict:
    """Independent EF (18+, observed non-work) and MD (16+, enterprise) paths."""
    evidence, unresolved, minor_md = [], not members, False
    for member in members:
        e01 = _code(member.get("e01_employed"), (1, 2))
        ef = _and(_age_at_least(member, 18), _e_applicable(member), None if e01 is None else e01 == 2)
        md = _observed_work_path(member, "e18_class_of_worker", MD_CLASSES, 16)
        if ef is True:
            evidence.append(_evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
                f"SLP EF: member aged {_integer(member['a07_age'])} recorded E01=2 (did not work in the reference week) in {survey_year}.", "EF"))
            for field, label in (("c07_tvet_graduate", "TVET graduate"), ("c08_tvet_currently_attending", "attending TVET")):
                if _code(member.get(field), (1, 2)) == 1:
                    evidence[-1]["observed_values"][field] = member[field]
                    evidence[-1]["explanation"] += f" Recorded {label}."
        if md is True:
            category = _code(member["e18_class_of_worker"], range(7))
            fields = ("a07_age", "b06_ofi", "e01_employed", "e18_class_of_worker")
            evidence.append(_evidence(member, fields,
                f"SLP MD: member aged {_integer(member['a07_age'])} recorded {MD_CLASSES[category]} (E18={category}) in {survey_year}.", "MD"))
            if _code(member.get("e11_nature_of_employment"), (1, 2, 3)) is not None:
                evidence[-1]["observed_values"]["e11_nature_of_employment"] = member["e11_nature_of_employment"]
            minor_md |= _integer(member["a07_age"]) in (16, 17)
        unresolved |= ef is None or md is None
    requirements = ("Written parental/legal guardian consent for a 16-17-year-old MD participant.",) if minor_md else ()
    notes = ("One or more CBMS pathways remain unresolved; supported EF/MD evidence still warrants review.",) if evidence and unresolved else ()
    return _result(household, "SLP", survey_year, evidence, unresolved, notes, requirements)


def screen_tupad(household: dict, members: list[dict], survey_year: int) -> dict:
    """Observed irregular work at age 18+ suggests disadvantaged-worker review."""
    evidence, unresolved = [], not members
    for member in members:
        trigger = _observed_work_path(member, "e11_nature_of_employment", IRREGULAR_WORK, 18)
        if trigger is True:
            nature = _code(member["e11_nature_of_employment"], (1, 2, 3))
            evidence.append(_evidence(member, ("a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment"),
                f"Member aged {_integer(member['a07_age'])} recorded {IRREGULAR_WORK[nature]} (E11={nature}) in {survey_year}."))
            if _code(member.get("e18_class_of_worker"), range(7)) is not None:
                evidence[-1]["observed_values"]["e18_class_of_worker"] = member["e18_class_of_worker"]
        unresolved |= trigger is None
    return _result(household, "TUPAD", survey_year, evidence, unresolved)


def screen_households(datasets: dict[str, pd.DataFrame], *, survey_year: int) -> pd.DataFrame:
    """Return exactly four independent results per household, in source order.

    Accept already-loaded data and explicit batch survey year. Validate unchanged
    UUID/member keys and Section L line associations, even while L07/L16 coding
    is pending. No names, MPI calculations, database writes or exports occur.
    """
    if isinstance(survey_year, bool) or not isinstance(survey_year, Integral) or survey_year <= 0:
        raise ValueError("An explicit positive integer survey year is required.")
    households, members = datasets["household"], datasets["section_a_e_indiv"]
    section = datasets["section_l02_to_l41"]
    _require_keys(households, ["uuid"], unique=True)
    member_fields = ("uuid", "line_number", "a07_age", "b06_ofi", "e01_employed",
                     "e11_nature_of_employment", "e18_class_of_worker")
    if any(field not in members for field in member_fields):
        raise ValueError("Required screening member columns are missing.")
    if any(field not in section for field in ("l07_currently_pregnant", "l16_pwd")):
        raise ValueError("Required Section L columns are missing.")
    optional = [field for field in ("a05_sex", "a02_relation_to_hh_head", "c07_tvet_graduate", "c08_tvet_currently_attending") if field in members]
    selected = members[[*member_fields, *optional]]
    evidence = section[["uuid", "section_l_line_number", "l07_currently_pregnant", "l16_pwd"]]
    joined = join_section_l(selected, evidence)
    roster_keys = pd.MultiIndex.from_frame(selected[["uuid", "line_number"]])
    section_keys = pd.MultiIndex.from_frame(evidence[["uuid", "section_l_line_number"]])
    if not selected["uuid"].isin(households["uuid"]).all() or not section_keys.isin(roster_keys).all():
        raise ValueError("Screening evidence contains members outside the household roster.")
    groups = {}
    for member in joined.to_dict("records"):
        groups.setdefault(member["uuid"], []).append(member)
    household_fields = [field for field in ("uuid", "l01_with_member_who_has_been_pregnant", "l06_with_member_currently_pregnant") if field in households]
    records = []
    functions = (screen_4ps, screen_slp, screen_spisc, screen_tupad)
    for household in households[household_fields].to_dict("records"):
        roster = groups.get(household["uuid"], [])
        for function in functions:
            records.append(function(household, roster, int(survey_year)))
    result = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    if len(result) != 4 * len(households) or result.duplicated(["household_uuid", "program_code"]).any():
        raise ValueError("Screening requires one result per household and program.")
    return result
