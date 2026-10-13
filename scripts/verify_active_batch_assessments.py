"""Execute the existing pilot engines once and print aggregate verification only."""

from collections import Counter
from fractions import Fraction
from hashlib import sha256
import json
from numbers import Integral
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from scripts.controlled_cbms_import_2022 import connect_verified
from services.active_batch_loader import load_active_batch
from services.assessments import combine_assessments
from services.screening.metadata import OUTPUT_COLUMNS
from services.subsidy_screening import PROGRAMS, STATUSES, screen_households
from services.vulnerability.scoring import (
    INDICATOR_WEIGHTS, METRIC_COLUMNS, assess_households, validate_weights,
)


class VerificationError(RuntimeError):
    """A fixed diagnostic code without household or evidence values."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def distribution(values):
    """Describe a numeric distribution without returning individual values."""
    return {
        "min": float(values.min()), "p25": float(values.quantile(0.25)),
        "median": float(values.median()), "p75": float(values.quantile(0.75)),
        "mean": float(values.mean()), "max": float(values.max()),
    }


def verify_mpi(households, mpi):
    """Audit emitted flags, weighted metrics and exact competition ranks."""
    validate_weights()
    require(len(mpi) == len(households) and mpi["uuid"].equals(households["uuid"])
            and not mpi["uuid"].duplicated().any(), "mpi_household_identity_mismatch")
    require(set(INDICATOR_WEIGHTS).issubset(mpi.columns)
            and set(METRIC_COLUMNS).issubset(mpi.columns), "mpi_output_columns_missing")
    exact_scores = []
    records = mpi.to_dict("records")
    for row in records:
        deprived, known = Fraction(0), Fraction(0)
        unknown = set()
        counts = Counter()
        for name, weight in INDICATOR_WEIGHTS.items():
            flag = row[name]
            require(flag is None or (isinstance(flag, Integral)
                    and not isinstance(flag, bool) and flag in (0, 1)), "invalid_mpi_flag")
            counts[flag] += 1
            if flag is None:
                unknown.add(name)
            else:
                known += weight
                if flag == 1:
                    deprived += weight
        score = 100 * deprived
        require(row["vulnerability_score"] == float(score)
                and row["known_weight"] == float(known)
                and row["coverage_pct"] == float(100 * known), "mpi_weighted_metrics_mismatch")
        require(row["deprived_indicator_count"] == counts[1]
                and row["not_deprived_indicator_count"] == counts[0]
                and row["unknown_indicator_count"] == counts[None], "mpi_flag_counts_mismatch")
        require(isinstance(row["unknown_zero_point_indicators"], tuple)
                and set(row["unknown_zero_point_indicators"]) == unknown
                and len(row["unknown_zero_point_indicators"]) == len(unknown),
                "mpi_unknown_evidence_mismatch")
        expected_status = "Needs Data Review" if unknown else "Complete Data"
        require(row["data_quality_status"] == expected_status
                and isinstance(row["assumptions_used"], tuple), "invalid_mpi_quality_metadata")
        exact_scores.append(score)
    groups = Counter(exact_scores)
    ranks, position = {}, 1
    for score in sorted(groups, reverse=True):
        ranks[score] = position
        position += groups[score]
    require(all(isinstance(row["vulnerability_rank"], Integral)
                and not isinstance(row["vulnerability_rank"], bool)
                and row["vulnerability_rank"] == ranks[score]
                for row, score in zip(records, exact_scores)), "mpi_competition_ranks_mismatch")
    bins = pd.cut(mpi["vulnerability_score"], range(0, 101, 10), include_lowest=True)
    histogram = {f"{'[' if lower == 0 else '('}{lower},{lower + 10}]": int(count)
                 for lower, count in zip(range(0, 100, 10), bins.value_counts(sort=False))}
    assumptions = {}
    for field in ("e01_assumed_not_working_count", "ofw_assumed_working_count", "b06_e01_conflict_count"):
        require(field in mpi and mpi[field].map(
            lambda value: isinstance(value, Integral) and not isinstance(value, bool) and value >= 0,
        ).all(), "invalid_mpi_assumption_counts")
        assumptions[field] = {"members": int(mpi[field].sum()),
                              "households": int(mpi[field].gt(0).sum())}
    return {
        "households": len(mpi), "indicators": len(INDICATOR_WEIGHTS),
        "score_distribution": distribution(mpi["vulnerability_score"]),
        "score_histogram": histogram, "coverage_pct": distribution(mpi["coverage_pct"]),
        "data_quality_counts": {status: int(mpi["data_quality_status"].eq(status).sum())
                                for status in ("Complete Data", "Needs Data Review")},
        "ranks": {"ranked_households": len(mpi), "min": min(ranks.values()),
                  "max": max(ranks.values()), "distinct_scores": len(groups),
                  "tied_score_groups": sum(count > 1 for count in groups.values()),
                  "households_in_ties": sum(count for count in groups.values() if count > 1)},
        "unknown_indicator_events": int(mpi["unknown_indicator_count"].sum()),
        "households_by_unknown_count": {int(count): int(total) for count, total in
                                        mpi["unknown_indicator_count"].value_counts().sort_index().items()},
        "indicator_counts": {name: {"deprived": int(mpi[name].eq(1).sum()),
                                    "not_deprived": int(mpi[name].eq(0).sum()),
                                    "unknown": int(mpi[name].isna().sum())}
                             for name in INDICATOR_WEIGHTS},
        "assumptions_and_conflicts": assumptions,
    }


def verify_screening(households, screening, survey_year):
    """Check complete four-program coverage, status and evidence containers."""
    require(set(OUTPUT_COLUMNS).issubset(screening.columns), "screening_columns_missing")
    require(len(screening) == 4 * len(households)
            and not screening.duplicated(["household_uuid", "program_code"]).any(),
            "screening_count_or_uniqueness_mismatch")
    require(screening["survey_year"].eq(survey_year).all()
            and screening["screening_status"].isin(STATUSES).all()
            and set(screening["program_code"]) == set(PROGRAMS), "invalid_screening_status_or_year")
    collections = ("trigger_reasons", "supporting_evidence", "triggering_member_line_numbers",
                   "key_unverified_requirements", "source_references", "methodology_notes",
                   "data_quality_warnings")
    for name in collections:
        require(screening[name].map(lambda value: isinstance(value, tuple)).all(),
                "unexpected_null_screening_evidence")
    statuses, warning_totals = {}, {}
    household_ids = set(households["uuid"])
    for program in PROGRAMS:
        rows = screening.loc[screening["program_code"].eq(program)]
        require(len(rows) == len(households) and set(rows["household_uuid"]) == household_ids,
                "screening_household_set_mismatch")
        statuses[program] = {status: int(rows["screening_status"].eq(status).sum()) for status in STATUSES}
        warnings = Counter()
        for status, evidence, reasons, issues in rows[
            ["screening_status", "supporting_evidence", "trigger_reasons", "data_quality_warnings"]
        ].itertuples(index=False, name=None):
            triggers = [item for item in evidence if item.get("evidence_role", "trigger") == "trigger"]
            require((status == "For Review") == bool(triggers)
                    and bool(reasons) == bool(triggers), "screening_trigger_status_mismatch")
            warnings.update(warning["code"] for warning in issues)
        warning_totals[program] = {
            "households_with_warnings": int(rows["data_quality_warnings"].map(bool).sum()),
            "warning_events": sum(warnings.values()), "by_code": dict(sorted(warnings.items())),
        }
    flagged = screening.loc[screening["screening_status"].eq("For Review"), "household_uuid"]
    return {"results": len(screening), "households": len(households), "survey_year": survey_year,
            "program_status_counts": statuses, "warnings": warning_totals,
            "households_with_any_review_signal": int(flagged.nunique()),
            "needs_data_review_results": int(screening["screening_status"].eq("Needs Data Review").sum())}


def verify_outputs(datasets, mpi, screening, survey_year):
    """Use the existing unified combiner without executing either engine again."""
    households = datasets["household"]
    mpi_summary = verify_mpi(households, mpi)
    screening_summary = verify_screening(households, screening, survey_year)
    combined = combine_assessments(mpi, screening, survey_year=survey_year)
    require(len(combined) == len(households), "unified_household_count_mismatch")
    return {"mpi": mpi_summary, "screening": screening_summary,
            "unified_households": len(combined)}


def source_fingerprint(datasets, references):
    """Keep in-memory source preservation fingerprints private."""
    return {name: sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()
                        + json.dumps(list(frame.columns)).encode()).digest()
            for name, frame in {**datasets, **references}.items()}


def process_memory():
    """Return current/peak process working set on Windows, when available."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in
            ("peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
             "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile")
        ]

    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
    memory_info.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not memory_info(current_process(), ctypes.byref(counters), counters.cb):
        return None
    return {"current_working_set_mib": round(counters.working_set / 2 ** 20, 2),
            "peak_working_set_mib": round(counters.peak_working_set / 2 ** 20, 2)}


def main():
    started, phase = perf_counter(), "read_only_loading"
    try:
        datasets, references, metadata = load_active_batch(connection_factory=connect_verified)
        require(metadata["batch_id"] == 1 and metadata["survey_year"] == 2022
                and metadata["municipality"] == "Pozorrubio"
                and len(datasets["household"]) == 19304, "unexpected_active_pilot_batch")
        timings = {"load_seconds": perf_counter() - started}
        fingerprint = source_fingerprint(datasets, references)
        phase, step = "mpi_execution", perf_counter()
        print(json.dumps({"progress": phase, "households": 19304}), flush=True)
        mpi = assess_households(datasets, references["mpi_mapping"])
        timings["mpi_seconds"] = perf_counter() - step
        phase, step = "screening_execution", perf_counter()
        print(json.dumps({"progress": phase, "programs": 4}), flush=True)
        screening = screen_households(datasets, survey_year=metadata["survey_year"])
        timings["screening_seconds"] = perf_counter() - step
        phase, step = "output_verification", perf_counter()
        summary = verify_outputs(datasets, mpi, screening, metadata["survey_year"])
        require(source_fingerprint(datasets, references) == fingerprint, "source_frames_changed")
        timings["verification_seconds"] = perf_counter() - step
        timings["total_seconds"] = perf_counter() - started
        members = datasets["section_a_e_indiv"]
        summary["source_missing_evidence"] = {
            "members_without_health_rows": len(members) - len(datasets["section_l02_to_l41"]),
            "blank_or_na_age_responses": int(members["a07_age"].str.strip().str.upper().isin(("", "NA")).sum()),
            "blank_or_na_e01_responses": int(members["e01_employed"].str.strip().str.upper().isin(("", "NA")).sum()),
        }
        print(json.dumps({"verification": "PASS", "database": "tanglaw_buhay_dev", "schema": "public",
                          "batch_id": metadata["batch_id"], "municipality": metadata["municipality"],
                          "summary": summary, "timings": {key: round(value, 3) for key, value in timings.items()},
                          "process_memory": process_memory(), "source_frames_unchanged": True}, indent=2))
        return 0
    except Exception as error:
        # Exception strings, frames and evidence can expose private input values.
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print(json.dumps({"verification": "FAIL", "blocked_phase": phase, "safe_error_code": code,
                          "elapsed_seconds": round(perf_counter() - started, 3)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
