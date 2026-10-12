"""Backward-compatible screening entry point: validate, join and run four programs.

Program rules live in services.screening; MPI scoring is independent.
"""

from numbers import Integral

import pandas as pd

from services.data_loader import _require_keys, join_section_l
from services.screening.four_ps import screen_4ps
from services.screening.slp import screen_slp
from services.screening.spisc import screen_spisc
from services.screening.tupad import screen_tupad
from services.screening.metadata import (
    CBMS_QUESTIONNAIRE, COMMON_NOTES, IRREGULAR_WORK, MD_CLASSES,
    OUTPUT_COLUMNS, PROGRAM_NOTES, PROGRAMS, REFERENCES,
    SLP_POLICY, STATUSES, TUPAD_BASE_POLICY, UNVERIFIED_REQUIREMENTS,
)


def screen_households(datasets: dict[str, pd.DataFrame], *, survey_year: int) -> pd.DataFrame:
    """Validate UUID/member joins and return four results per household in source order."""
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
    optional_fields = [
        field for field in ("a05_sex", "a02_relation_to_hh_head",
                            "c07_tvet_graduate", "c08_tvet_currently_attending")
        if field in members
    ]
    selected = members[[*member_fields, *optional_fields]]
    section_evidence = section[["uuid", "section_l_line_number", "l07_currently_pregnant", "l16_pwd"]]
    joined = join_section_l(selected, section_evidence)
    roster_keys = pd.MultiIndex.from_frame(selected[["uuid", "line_number"]])
    section_keys = pd.MultiIndex.from_frame(section_evidence[["uuid", "section_l_line_number"]])
    if not selected["uuid"].isin(households["uuid"]).all() or not section_keys.isin(roster_keys).all():
        raise ValueError("Screening evidence contains members outside the household roster.")
    members_by_household = {}
    for member in joined.to_dict("records"):
        members_by_household.setdefault(member["uuid"], []).append(member)
    household_fields = [
        field for field in ("uuid", "l01_with_member_who_has_been_pregnant",
                            "l06_with_member_currently_pregnant", "l15_with_pwd_member")
        if field in households
    ]
    records = []
    screening_functions = (screen_4ps, screen_slp, screen_spisc, screen_tupad)
    for household in households[household_fields].to_dict("records"):
        roster = members_by_household.get(household["uuid"], [])
        for function in screening_functions:
            records.append(function(household, roster, int(survey_year)))
    result = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    if len(result) != 4 * len(households) or result.duplicated(["household_uuid", "program_code"]).any():
        raise ValueError("Screening requires one result per household and program.")
    return result
