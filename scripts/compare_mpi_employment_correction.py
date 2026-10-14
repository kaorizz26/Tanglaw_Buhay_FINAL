"""Read-only pilot impact comparison; household findings stay only in memory."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from repositories import assessment_runs as repository
from scripts.controlled_cbms_import_2022 import connect_verified, file_hashes
from scripts.verify_active_batch_assessments import (
    VerificationError, distribution, require, source_fingerprint, verify_mpi,
)
from services.active_batch_loader import load_active_batch
from services.assessment_queries import _resolve_context
from services.vulnerability.scoring import INDICATOR_WEIGHTS, assess_households


EMPLOYMENT = ("Employment Proxy", "Underemployment Proxy", "Working Child Not in School")
FUNCTIONS = ("employment_deprivation", "underemployment", "working_child_not_in_school")
BASELINE_CHECKPOINT = "54e682a"


def read_stored(metadata):
    """Read the completed pilot run under the existing local/read-only guard."""
    with connect_verified() as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        context = _resolve_context(connection)
        require(context["batch_id"] == metadata["batch_id"] == 1 and context["run_id"] == 1
                and context["survey_year"] == metadata["survey_year"] == 2022
                and context["municipality"] == metadata["municipality"] == "Pozorrubio"
                and context["source_provenance"] == metadata["source_provenance"],
                "unexpected_stored_pilot_context")
        rows = list(repository.iter_household_assessments(connection, context["run_id"]))
    require(len(rows) == 19304, "stored_household_count_mismatch")
    return rows


def findings_digest(rows):
    """Private preservation digest; never return findings through console output."""
    digest = sha256()
    for row in rows:
        digest.update(json.dumps(row, sort_keys=True).encode())
    return digest.digest()


def imputation_only_counterfactual(households, members):
    """Reuse checkpoint logic, removing only its missing-E01 nonwork imputation."""
    result = subprocess.run(
        ["git", "show", f"{BASELINE_CHECKPOINT}:services/vulnerability/employment.py"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    legacy = ModuleType("historical_mpi_employment")
    exec(compile(result.stdout, "historical_mpi_employment", "exec"), legacy.__dict__)
    original_evidence = legacy._work_evidence

    def unresolved_evidence(age, b06, work):
        applicable, worked, assumed = original_evidence(age, b06, work)
        return (applicable, None, False) if assumed else (applicable, worked, assumed)

    legacy._work_evidence = unresolved_evidence
    return {name: getattr(legacy, function)(households, members).tolist()
            for name, function in zip(EMPLOYMENT, FUNCTIONS)}


def transitions(before, after):
    """Count indicator transitions without household identifiers."""
    def label(value):
        return "Unknown" if value is None else str(value)

    return dict(sorted(Counter(f"{label(old)} -> {label(new)}"
                               for old, new in zip(before, after)).items()))


def main():
    started, phase = perf_counter(), "read_only_loading"
    try:
        hashes = file_hashes()
        datasets, references, metadata = load_active_batch(connection_factory=connect_verified)
        households, members = datasets["household"], datasets["section_a_e_indiv"]
        require(len(households) == 19304, "unexpected_household_count")
        fingerprint = source_fingerprint(datasets, references)
        stored = read_stored(metadata)
        before_digest = findings_digest(stored)
        old = pd.DataFrame([row["mpi"] for row in stored], dtype=object)
        require(set(old["uuid"]) == set(households["uuid"]), "stored_household_set_mismatch")
        # Pandas 3 can infer a string dtype when resetting the UUID index.
        old = old.set_index("uuid").loc[households["uuid"]].reset_index().astype(object)
        phase = "in_memory_comparison"
        old_summary = verify_mpi(households, old)
        corrected = assess_households(datasets, references["mpi_mapping"])
        new_summary = verify_mpi(households, corrected)
        for name in set(INDICATOR_WEIGHTS) - set(EMPLOYMENT):
            require(old[name].tolist() == corrected[name].tolist(), "unrelated_indicator_changed")
        counterfactual = imputation_only_counterfactual(households, members)
        changes = {}
        for name in EMPLOYMENT:
            before, after = old[name].tolist(), corrected[name].tolist()
            changes[name] = {
                "before": old_summary["indicator_counts"][name],
                "after": new_summary["indicator_counts"][name],
                "transitions": transitions(before, after),
                "previous_outcomes_dependent_on_nonwork_imputation": sum(
                    left != right for left, right in zip(before, counterfactual[name])),
                "additional_age_scope_changes": sum(
                    left != right for left, right in zip(counterfactual[name], after)),
            }
        score_delta = corrected["vulnerability_score"].astype(float) - old["vulnerability_score"].astype(float)
        rank_delta = corrected["vulnerability_rank"].astype(int) - old["vulnerability_rank"].astype(int)
        require(corrected["e01_assumed_not_working_count"].eq(0).all(), "nonwork_imputation_remains")
        require(old["ofw_assumed_working_count"].tolist() == corrected["ofw_assumed_working_count"].tolist(),
                "ofw_assumption_changed")
        require(source_fingerprint(datasets, references) == fingerprint, "source_frames_changed")
        phase = "read_only_preservation_verification"
        require(findings_digest(read_stored(metadata)) == before_digest, "stored_completed_run_changed")
        require(file_hashes() == hashes, "source_files_or_configuration_changed")
        report = {
            "comparison": "PASS", "database": "tanglaw_buhay_dev", "batch_id": 1,
            "stored_run_id": 1, "households": 19304, "indicators": changes,
            "previous_imputation_audit": {
                "members": int(old["e01_assumed_not_working_count"].sum()),
                "households": int(old["e01_assumed_not_working_count"].gt(0).sum())},
            "scores": {"before": old_summary["score_distribution"], "after": new_summary["score_distribution"],
                       "increased": int(score_delta.gt(1e-10).sum()), "decreased": int(score_delta.lt(-1e-10).sum()),
                       "unchanged": int(score_delta.abs().le(1e-10).sum()), "delta": distribution(score_delta)},
            "completeness": {"before": old_summary["data_quality_counts"], "after": new_summary["data_quality_counts"],
                             "unknown_before": old_summary["unknown_indicator_events"],
                             "unknown_after": new_summary["unknown_indicator_events"],
                             "coverage_before": old_summary["coverage_pct"], "coverage_after": new_summary["coverage_pct"]},
            "ranks": {"before": old_summary["ranks"], "after": new_summary["ranks"],
                      "improved": int(rank_delta.lt(0).sum()), "worsened": int(rank_delta.gt(0).sum()),
                      "unchanged": int(rank_delta.eq(0).sum()), "delta": distribution(rank_delta)},
            "ofw_assumed_working_members_preserved": int(corrected["ofw_assumed_working_count"].sum()),
            "other_13_indicators_unchanged": True, "stored_run_and_source_files_preserved": True,
            "elapsed_seconds": round(perf_counter() - started, 3),
        }
        print(json.dumps(report), flush=True)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print(json.dumps({"comparison": "FAIL", "phase": phase, "safe_error_code": code}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
