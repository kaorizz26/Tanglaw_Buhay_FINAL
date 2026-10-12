"""Historical four-program CBMS pre-screening, independent of MPI scoring.

These project triggers suggest staff consideration, never official eligibility,
approval, assigned cases, or required follow-up. Original values are read-only.
Age is the recorded survey age; no age/pregnancy/work condition is projected to
today. SLP requires observed work status. TUPAD separately derives the approved
missing-E01 pilot assumption from raw responses, never importing MPI results.
Synthetic E15 is never used.

Phase 5B provisionally interprets L07/L16 as dataset-specific Yes/No responses,
not PSA-confirmed recoding: the questionnaire describes member line numbers.
Member pregnancy/disability evidence requires household L06/L15 corroboration
and valid roster joins. Household context never proves a senior's disability.
Contradictions are retained as informational warnings, without assigned tasks.
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
            "L06=1 adds historical pregnancy review; conflicting L01=2/L06=1 remains unresolved unless an independent child trigger applies.",
            "L07=1 is a provisional dataset-specific Yes interpretation, corroborated by L06=1 and female age 10+; not PSA-confirmed recoding."),
    "SLP": ("EF uses recorded E01=2, not official unemployment or MPI imputation.",
            "MD uses recorded E01=1 and E18=3/4/5/6 with verified Section E applicability.",
            "E03/job-attachment routing is absent from the export; missing E11/E18 cannot be declared a verified skip solely from E01=2.",
            "MC 003 s.2025 pp.8-9 provides MD age 16+ and EF age 18+; these work-status triggers are project heuristics."),
    "SPISC": ("Age 60+ is a review signal, not proof of indigency or pension eligibility.",
              "L15=1 is household disability context; only a senior's own joined L16=1 corroborated by L15=1 supports senior-specific disability.",
              "L16=1 is a provisional dataset-specific Yes interpretation, not PSA-confirmed recoding; optional support never changes the age trigger.",
              "The FO XI page describes older guidance; MC 022 s.2026 is listed, but its full text has not been verified."),
    "TUPAD": ("At age 18+, applicable observed E01=2 or missing E01 assumed nonworking are additional approved pilot review triggers.",
              "B06 1/2/3 with missing E01 is assumed overseas employment, not a nonwork trigger; B06 6 stays non-applicable.",
              "Observed irregular work still requires valid employment evidence. Assumptions are labeled and do not prove unemployment or eligibility.",
              "Age 18+ follows DO 239-23 section 8, with no invented upper age limit.",
              "DO 239-E s.2026 is listed, but its full text has not been verified; no new exclusions are invented.",
              "Missing E11 does not erase a supported nonwork trigger; contradictory or genuinely indeterminate evidence remains unresolved."),
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
    "data_quality_warnings",
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


def _missing(value: object) -> bool:
    """Recognize null/blank/literal NA, separating absence from invalid codes."""
    return bool(pd.isna(value)) or (isinstance(value, str) and value.strip().upper() in {"", "NA"})


def _warning(code: str, message: str, member: dict | None = None) -> dict:
    """Attach an informational data issue, never a case or verification task."""
    return {"code": code, "member_line_number": member["line_number"] if member else None,
            "message": message}


def _household_evidence(household: dict, field: str, explanation: str, trigger_code: str,
                        role: str = "trigger") -> dict:
    return {"member_line_number": None, "source_dataset": "household",
            "observed_values": {field: household[field]}, "explanation": explanation,
            "trigger_code": trigger_code, "evidence_role": role, "evidence_type": "observed"}


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


def _result(household: dict, program: str, year: int, evidence: list, unresolved: bool, extra_notes=(), extra_requirements=(), warnings=()) -> dict:
    """Supported observed/pilot triggers take precedence over unrelated gaps."""
    triggers = [item for item in evidence if item.get("evidence_role", "trigger") == "trigger"]
    status = STATUSES[0] if triggers else (STATUSES[2] if unresolved else STATUSES[1])
    lines = tuple(dict.fromkeys(item["member_line_number"] for item in triggers if item["member_line_number"] is not None))
    reasons = tuple(dict.fromkeys(item["explanation"] for item in triggers))
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
        "data_quality_warnings": tuple(warnings),
    }


def screen_4ps(household: dict, members: list[dict], survey_year: int) -> dict:
    """Child age or consistent household L06=1 suggests historical 4Ps review.

    L07 attribution additionally requires its own valid joined row, female sex,
    age 10+ and L06 corroboration. Missing attribution does not erase a valid
    household trigger. Direct L01/L06 skip contradictions are not confirmed
    pregnancy evidence; independent child evidence retains priority.
    """
    evidence, warnings, unresolved = [], [], not members
    pregnant = _code(household.get("l06_with_member_currently_pregnant"), (1, 2))
    ever = _code(household.get("l01_with_member_who_has_been_pregnant"), (1, 2))
    routing_conflict = pregnant == 1 and ever == 2
    if routing_conflict:
        warnings.append(_warning("pregnancy_routing_conflict", "L01=2 skips pregnancy questions but L06=1 is populated."))
        unresolved = True
    elif pregnant == 1:
        evidence.append(_household_evidence(household, "l06_with_member_currently_pregnant",
            f"Household recorded a pregnant member (L06=1) in the {survey_year} CBMS survey.", "household_pregnancy"))
    attributed = 0
    for member in members:
        age = _integer(member.get("a07_age"))
        if age is not None and age <= 18:
            evidence.append(_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        unresolved |= age is None
        sex = _code(member.get("a05_sex"), (1, 2))
        potential = _and(_age_at_least(member, 10), None if sex is None else sex == 2)
        raw = member.get("l07_currently_pregnant")
        affected = _code(raw, (1, 2))
        present = not _missing(member.get("section_l_line_number"))
        if affected == 1:
            if pregnant == 1 and not routing_conflict and potential is True and present:
                item = _evidence(member, ("a07_age", "a05_sex", "l07_currently_pregnant"),
                    f"Joined female member aged {age} has provisional L07=1, corroborated by L06=1 in {survey_year}.")
                item.update(source_dataset="section_l02_to_l41", evidence_type="provisional_pilot",
                            trigger_code="member_pregnancy", corroborating_household_values={"l06_with_member_currently_pregnant": household["l06_with_member_currently_pregnant"]})
                evidence.append(item)
                attributed += 1
            else:
                warnings.append(_warning("pregnancy_member_conflict", "L07=1 lacks consistent household, female age 10+, or joined-member corroboration.", member))
                unresolved = True
        elif affected is None and not _missing(raw):
            warnings.append(_warning("pregnancy_invalid_member_code", "L07 is not valid under the provisional Yes/No interpretation.", member))
            unresolved = True
        elif potential is not False and pregnant != 2 and ever != 2:
            if pregnant != 1:
                unresolved = True
    if pregnant == 1 and not routing_conflict and not attributed:
        candidates = [member for member in members if _code(member.get("a05_sex"), (1, 2)) == 2
                      and _age_at_least(member, 10) is True]
        complete_demographics = all(_integer(member.get("a07_age")) is not None
                                    and _code(member.get("a05_sex"), (1, 2)) is not None for member in members)
        if candidates and complete_demographics and all(
                not _missing(member.get("section_l_line_number"))
                and _code(member.get("l07_currently_pregnant"), (1, 2)) == 2 for member in candidates):
            warnings.append(_warning("pregnancy_household_member_conflict", "L06=1 conflicts with all relevant joined L07=2 responses; only the household signal is retained."))
        else:
            warnings.append(_warning("pregnancy_member_unresolved", "Household pregnancy is recorded, but no member can be safely attributed."))
    return _result(household, "4Ps", survey_year, evidence, unresolved, warnings=warnings)


def screen_spisc(household: dict, members: list[dict], survey_year: int) -> dict:
    """Keep age 60+ unchanged; distinguish household and senior-specific PWD.

    Supporting disability gaps/contradictions are warnings, not additional age
    triggers or mandatory follow-up. A different member's PWD cannot describe
    a senior. Only corroborated, joined senior L16=1 is attributed provisionally.
    """
    evidence, warnings, unresolved = [], [], not members
    disability = _code(household.get("l15_with_pwd_member"), (1, 2))
    if disability == 1:
        evidence.append(_household_evidence(household, "l15_with_pwd_member",
            f"Household recorded a member with disability (L15=1) in {survey_year}; this does not identify a senior.",
            "household_disability", "supporting_context"))
    for member in members:
        age = _integer(member.get("a07_age"))
        if age is not None and age >= 60:
            evidence.append(_evidence(member, ("a07_age",),
                f"Household member aged {age} was recorded in the {survey_year} CBMS survey."))
        unresolved |= age is None
        raw = member.get("l16_pwd")
        pwd = _code(raw, (1, 2))
        present = not _missing(member.get("section_l_line_number"))
        if pwd == 1:
            if disability != 1 or not present:
                warnings.append(_warning("disability_member_conflict", "L16=1 lacks household L15=1 or joined-member corroboration.", member))
            elif age is not None and age >= 60:
                item = _evidence(member, ("a07_age", "l16_pwd"),
                    f"This senior's joined record has provisional L16=1, corroborated by household L15=1 in {survey_year}.")
                item.update(source_dataset="section_l02_to_l41", evidence_role="supporting_context",
                            evidence_type="provisional_pilot", trigger_code="senior_disability",
                            corroborating_household_values={"l15_with_pwd_member": household["l15_with_pwd_member"]})
                evidence.append(item)
            elif age is None:
                warnings.append(_warning("senior_disability_unresolved", "Age is unknown; disability cannot be attributed to a senior.", member))
        elif pwd is None and not _missing(raw):
            warnings.append(_warning("disability_invalid_member_code", "L16 is not valid under the provisional Yes/No interpretation.", member))
        elif disability == 1 and age is not None and age >= 60 and (pwd is None or not present):
            warnings.append(_warning("senior_disability_unresolved", "Household disability is recorded, but this senior's disability response is unavailable.", member))
    if disability == 1 and members and all(
            not _missing(member.get("section_l_line_number"))
            and _code(member.get("l16_pwd"), (1, 2)) == 2 for member in members):
        warnings.append(_warning("disability_household_member_conflict", "L15=1 conflicts with all joined L16=2 responses; no member is attributed."))
    return _result(household, "SPISC", survey_year, evidence, unresolved, warnings=warnings)


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
    """Age 18+ irregular-work or nonwork signals under approved pilot rules.

    Applicable E01=2 is observed nonwork; applicable missing E01 is explicitly
    assumed nonwork. Missing E01 alongside populated employment details is
    conflicting evidence, not an authorized nonwork imputation. B06 1/2/3
    missing E01 is overseas-employment context only; B06 6 stays excluded.
    Missing E11 cannot erase a confirmed independent nonwork trigger. None of
    these signals establish current displacement, unemployment or eligibility.
    """
    evidence, warnings, unresolved = [], [], not members
    for member in members:
        adult = _age_at_least(member, 18)
        if adult is False:
            continue
        b06 = _code(member.get("b06_ofi"), range(1, 8))
        raw_work = member.get("e01_employed")
        work = _code(raw_work, (1, 2))
        details_present = any(not _missing(member.get(field)) for field in
                              ("e11_nature_of_employment", "e18_class_of_worker"))
        if b06 in (1, 2, 3, 6):
            conflict = not _missing(raw_work) or details_present
            if conflict:
                warnings.append(_warning("employment_skip_conflict", "Employment responses are populated despite a B06 Section E skip.", member))
                unresolved |= b06 != 6
            elif b06 in (1, 2, 3) and adult is True:
                item = _evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
                    f"B06={b06} with missing E01 is ASSUMED overseas employment for the {survey_year} pilot; not a nonwork trigger.")
                item.update(evidence_type="pilot_assumption", evidence_role="supporting_context",
                            trigger_code="assumed_overseas_work")
                evidence.append(item)
            continue  # B06 6 is non-applicable; overseas context is not a trigger.
        if b06 is None or adult is None:
            unresolved = True
            continue
        if work == 2:
            item = _evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
                f"Member aged {_integer(member['a07_age'])} recorded E01=2 (did not work in the reference week) in {survey_year}; observed pilot review signal.")
            item.update(evidence_type="observed", trigger_code="observed_nonwork")
            evidence.append(item)
            continue  # Missing E11 is irrelevant to this established trigger.
        if _missing(raw_work):
            if details_present:
                warnings.append(_warning("nonwork_imputation_conflict", "E01 is missing but employment details are populated; nonwork is not assumed.", member))
                unresolved = True
            else:
                item = _evidence(member, ("a07_age", "b06_ofi", "e01_employed"),
                    f"Member aged {_integer(member['a07_age'])} has applicable missing E01 in {survey_year}; ASSUMED not working under the pilot, not an observed response.")
                item.update(evidence_type="pilot_assumption", trigger_code="assumed_nonwork")
                evidence.append(item)
            continue
        nature = _code(member.get("e11_nature_of_employment"), (1, 2, 3))
        if work == 1 and nature in IRREGULAR_WORK:
            item = _evidence(member, ("a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment"),
                f"Member aged {_integer(member['a07_age'])} recorded {IRREGULAR_WORK[nature]} (E11={nature}) in {survey_year}.")
            item.update(evidence_type="observed", trigger_code="irregular_work")
            evidence.append(item)
            if _code(member.get("e18_class_of_worker"), range(7)) is not None:
                evidence[-1]["observed_values"]["e18_class_of_worker"] = member["e18_class_of_worker"]
        elif work != 1 or nature is None:
            unresolved = True
    return _result(household, "TUPAD", survey_year, evidence, unresolved, warnings=warnings)


def screen_households(datasets: dict[str, pd.DataFrame], *, survey_year: int) -> pd.DataFrame:
    """Return exactly four independent results per household, in source order.

    Accept already-loaded data and explicit batch survey year. Validate unchanged
    UUID/member keys and Section L line associations before provisional L07/L16
    attribution. No names, MPI calculations, database writes or exports occur.
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
    household_fields = [field for field in ("uuid", "l01_with_member_who_has_been_pregnant", "l06_with_member_currently_pregnant", "l15_with_pwd_member") if field in households]
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
