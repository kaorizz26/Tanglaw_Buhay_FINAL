"""Combine unchanged MPI and screening outputs by UUID, without new scoring."""

from numbers import Integral

import pandas as pd

from services.data_loader import _require_keys
from services.subsidy_screening import PROGRAMS, screen_households
from services.vulnerability.scoring import assess_households


def combine_assessments(mpi: pd.DataFrame, screening: pd.DataFrame, *, survey_year: int) -> dict:
    """Retain every output field and nested payload, in MPI household order.

    Both inputs must cover the same batch. Ranks belong to that original batch;
    later UI filters must never calculate replacement ranks.
    """
    if isinstance(survey_year, bool) or not isinstance(survey_year, Integral) or survey_year <= 0:
        raise ValueError("An explicit positive integer survey year is required.")
    _require_keys(mpi, ["uuid"], unique=True)
    _require_keys(screening, ["household_uuid", "program_code"], unique=True)
    if set(screening["household_uuid"]) != set(mpi["uuid"]):
        raise ValueError("MPI and screening must contain the same households.")
    if set(screening["program_code"]) != set(PROGRAMS):
        raise ValueError("The four approved program results are required.")
    if "survey_year" not in screening or not screening["survey_year"].eq(survey_year).all():
        raise ValueError("Screening survey years must match the source batch.")

    programs_by_household = {}
    for row in screening.to_dict("records"):
        programs_by_household.setdefault(row["household_uuid"], {})[row["program_code"]] = row
    combined = {}
    for row in mpi.to_dict("records"):
        uuid = row["uuid"]
        programs = programs_by_household[uuid]
        if set(programs) != set(PROGRAMS):
            raise ValueError("Each household requires exactly four program results.")
        combined[uuid] = {
            "household_uuid": uuid,
            "survey_year": int(survey_year),
            "mpi": row,
            "programs": {program: programs[program] for program in PROGRAMS},
        }
    return combined


def build_unified_assessments(datasets: dict, references: dict, *, survey_year: int) -> dict:
    """Calculate each existing engine once and combine its full results."""
    mpi = assess_households(datasets, references["mpi_mapping"])
    screening = screen_households(datasets, survey_year=survey_year)
    return combine_assessments(mpi, screening, survey_year=survey_year)
