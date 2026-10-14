"""Prepared corrected MPI version runner; execution requires separate owner approval."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from psycopg import sql

from repositories import assessment_runs as repository, dataset_batches
from scripts.compare_mpi_employment_correction import findings_digest, EMPLOYMENT
from scripts.controlled_cbms_import_2022 import (
    EXPECTED, TABLES, connect_verified, file_hashes, table_counts, verify_schema,
)
from scripts.controlled_assessment_persistence_2022 import (
    assessment_schema_present, calculation_provenance, preservation_snapshot,
)
from scripts.verify_active_batch_assessments import (
    VerificationError, require, source_fingerprint, verify_mpi, verify_screening,
)
from services.active_batch_loader import _batch_metadata, load_active_batch
from services.vulnerability.scoring import INDICATOR_WEIGHTS, assess_households


VERSION = "tanglaw-buhay-mpi-e01-unknown-v2"
BASELINE_VERSION = "tanglaw-buhay-approved-mpi-screening-v1"
MIGRATION = ROOT / "migrations/0004_assessment_run_selection.sql"
PHASE = "configuration"
COMMITTED = False


def selection_schema_present(connection):
    """Check the selection table without creating, seeding or repairing it."""
    if not connection.execute("SELECT to_regclass('public.assessment_run_selections')").fetchone()[0]:
        return False
    columns = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='assessment_run_selections' ORDER BY ordinal_position",
    ).fetchall()
    require(tuple(row[0] for row in columns) == ("batch_id", "run_id", "selected_at"),
            "selection_schema_mismatch")
    constraints = connection.execute(
        "SELECT con.contype, count(*), bool_and(con.convalidated) FROM pg_constraint con "
        "WHERE con.conrelid='public.assessment_run_selections'::regclass GROUP BY con.contype",
    ).fetchall()
    catalog = {kind: (count, validated) for kind, count, validated in constraints}
    require(catalog.get("p") == (1, True) and catalog.get("f") == (2, True),
            "selection_constraints_mismatch")
    return True


def preflight(connection):
    """Require the unchanged pilot baseline and no prior/partial replacement attempt."""
    verify_schema(connection)
    require(assessment_schema_present(connection), "assessment_schema_missing")
    metadata = _batch_metadata(dataset_batches.find_active_batches(connection))
    require(metadata["batch_id"] == 1 and metadata["survey_year"] == 2022
            and metadata["municipality"] == "Pozorrubio" and table_counts(connection) == EXPECTED,
            "unexpected_pilot_source")
    baseline = repository.find_run(connection, 1)
    require(baseline is not None and baseline["batch_id"] == 1 and baseline["status"] == "completed"
            and baseline["calculation_version"] == BASELINE_VERSION
            and baseline["expected_household_count"] == 19304, "baseline_run_mismatch")
    counts = connection.execute(
        "SELECT (SELECT count(*) FROM assessment_runs WHERE batch_id=1 AND id<>1), "
        "(SELECT count(*) FROM household_assessments WHERE run_id=1), "
        "(SELECT count(*) FROM household_assessments h, LATERAL jsonb_each(h.findings->'programs') p "
        "WHERE h.run_id=1)",
    ).fetchone()
    require(counts == (0, 19304, 77216), "existing_replacement_or_baseline_coverage_error")
    present = selection_schema_present(connection)
    if present:
        selection = connection.execute(
            "SELECT run_id FROM assessment_run_selections WHERE batch_id=1",
        ).fetchone()
        require(selection in (None, (1,)), "unexpected_existing_selection")
    return metadata, baseline, present


def build_corrected_findings(datasets, references, baseline, survey_year):
    """Calculate MPI only and copy every existing subsidy payload without mutation."""
    households = datasets["household"]
    require(set(baseline) == set(households["uuid"]), "baseline_household_set_mismatch")
    require(all(row["survey_year"] == survey_year for row in baseline.values()), "baseline_year_mismatch")
    mpi = assess_households(datasets, references["mpi_mapping"])
    mpi_summary = verify_mpi(households, mpi)
    mpi_records = mpi.to_dict("records")
    for row in mpi_records:
        previous = baseline[row["uuid"]]["mpi"]
        require(all(row[name] == previous[name] for name in set(INDICATOR_WEIGHTS) - set(EMPLOYMENT)),
                "unrelated_mpi_indicator_changed")
        require(row["e01_assumed_not_working_count"] == 0
                and row["ofw_assumed_working_count"] == previous["ofw_assumed_working_count"],
                "employment_correction_audit_mismatch")
    screening = pd.DataFrame([program for finding in baseline.values()
                              for program in finding["programs"].values()], dtype=object)
    screening_summary = verify_screening(households, screening, survey_year)
    findings = deepcopy(baseline)
    for row in mpi_records:
        findings[row["uuid"]]["mpi"] = row
    return findings, {"mpi": mpi_summary, "screening": screening_summary}


def verify_corrected_baseline(summary):
    """Require the previously approved read-only comparison aggregates before storing."""
    mpi, screening = summary["mpi"], summary["screening"]
    require(mpi["households"] == 19304 and screening["results"] == 77216,
            "corrected_coverage_mismatch")
    expected = {"Employment Proxy": (2026, 14804, 2474), "Underemployment Proxy": (1035, 15308, 2961),
                "Working Child Not in School": (51, 19069, 184)}
    for name, counts in expected.items():
        actual = mpi["indicator_counts"][name]
        require(tuple(actual[key] for key in ("deprived", "not_deprived", "unknown")) == counts,
                "corrected_indicator_baseline_mismatch")
    require(mpi["data_quality_counts"] == {"Complete Data": 15238, "Needs Data Review": 4066}
            and mpi["unknown_indicator_events"] == 5690
            and abs(mpi["score_distribution"]["mean"] - 6.857099277064051) < 1e-8
            and abs(mpi["coverage_pct"]["mean"] - 97.53339837454529) < 1e-8
            and mpi["ranks"]["max"] == 13117, "corrected_mpi_baseline_mismatch")


def verify_version(connection, batch_id, run_id, expected):
    """Compare every complete retrieved finding and require the explicit designation."""
    stored = {row["household_uuid"]: row for row in repository.iter_household_assessments(connection, run_id)}
    require(stored == expected, "corrected_stored_findings_mismatch")
    counts = connection.execute(
        "SELECT (SELECT count(*) FROM household_assessments WHERE run_id=%s), "
        "(SELECT count(*) FROM household_assessments h, LATERAL jsonb_each(h.findings->'programs') p "
        "WHERE h.run_id=%s)", (run_id, run_id),
    ).fetchone()
    require(counts == (len(expected), 4 * len(expected)), "corrected_stored_counts_mismatch")
    selected = repository.find_designated_run(connection, batch_id)
    require(selected is not None and selected["run_id"] == run_id
            and selected["calculation_version"] == VERSION, "corrected_designation_mismatch")


def persist_version(connection, batch_id, findings, provenance):
    """Store, complete, designate and verify together; failure rolls back the whole unit."""
    with connection.transaction():
        run_id = repository.create_run(connection, batch_id, calculation_version=VERSION,
                                       calculation_provenance=provenance)
        repository.store_household_assessments(connection, run_id, findings)
        repository.complete_run(connection, run_id)
        repository.designate_run(connection, batch_id, run_id)
        verify_version(connection, batch_id, run_id, findings)
    return run_id


def execute_once(expected_snapshot, expected_hashes):
    """Prepared write path, never invoked by checks/tests against the development database."""
    global PHASE, COMMITTED
    with connect_verified() as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ WRITE")
        connection.execute("SELECT pg_advisory_xact_lock(6042022)")
        for table in ("dataset_batches", *TABLES.values(), "staff_users"):
            connection.execute(sql.SQL("LOCK TABLE public.{} IN SHARE MODE").format(sql.Identifier(table)))
        connection.execute("LOCK TABLE assessment_runs, household_assessments IN SHARE ROW EXCLUSIVE MODE")
        if selection_schema_present(connection):
            connection.execute("LOCK TABLE assessment_run_selections IN SHARE ROW EXCLUSIVE MODE")
        metadata, baseline_metadata, present = preflight(connection)
        require(preservation_snapshot(connection) == expected_snapshot, "source_or_staff_changed")
        baseline_rows = list(repository.iter_household_assessments(connection, 1))
        before_digest = findings_digest(baseline_rows)
        baseline = {row["household_uuid"]: row for row in baseline_rows}
        require(len(baseline) == 19304, "baseline_household_uniqueness_error")
        datasets, references, loaded = load_active_batch(connection_factory=connect_verified)
        require(loaded == metadata, "source_metadata_changed")
        fingerprint = source_fingerprint(datasets, references)
        PHASE = "corrected_mpi_in_memory"
        findings, summary = build_corrected_findings(datasets, references, baseline, 2022)
        verify_corrected_baseline(summary)
        require(source_fingerprint(datasets, references) == fingerprint, "source_frames_changed")
        require(file_hashes() == expected_hashes, "source_files_or_configuration_changed")
        PHASE = "atomic_corrected_version_and_selection"
        if not present:
            connection.execute(MIGRATION.read_text(encoding="utf-8"))
        require(selection_schema_present(connection), "selection_migration_missing")
        provenance = calculation_provenance(metadata)
        provenance.update(authorization="Owner-approved corrected MPI v2 execution",
                          mpi_version=VERSION, subsidy_source_run_id=1,
                          subsidy_version=BASELINE_VERSION,
                          mpi_correction_checkpoint="f8a9362",
                          subsidy_handling="All four full subsidy findings copied unchanged from Run 1",
                          verification_baseline="Approved MPI correction comparison; unchanged Run 1 subsidy findings",
                          selection_migration_sha256=sha256(MIGRATION.read_bytes()).hexdigest())
        run_id = persist_version(connection, 1, findings, provenance)
        require(repository.find_run(connection, 1) == baseline_metadata
                and findings_digest(list(repository.iter_household_assessments(connection, 1))) == before_digest,
                "historical_run_changed")
        require(preservation_snapshot(connection) == expected_snapshot, "source_or_staff_changed")
        require(file_hashes() == expected_hashes, "source_files_or_configuration_changed")
    COMMITTED = True
    PHASE = "read_only_post_commit_verification"
    with connect_verified() as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        verify_version(connection, 1, run_id, findings)
        require(repository.find_run(connection, 1) == baseline_metadata
                and findings_digest(list(repository.iter_household_assessments(connection, 1))) == before_digest,
                "historical_run_changed_after_commit")
        require(preservation_snapshot(connection) == expected_snapshot, "source_or_staff_changed_after_commit")
    require(file_hashes() == expected_hashes, "files_changed_after_commit")
    return {"execution": "SUCCESS", "run_id": run_id, "batch_id": 1, "version": VERSION,
            "households": 19304, "subsidy_findings": 77216, "historical_run_preserved": True,
            "source_and_staff_preserved": True, "summary": summary}


def main(arguments=None):
    """An explicit mode is required; checks never calculate, migrate or write."""
    global PHASE, COMMITTED
    PHASE, COMMITTED = "read_only_preflight", False
    started = perf_counter()
    try:
        arguments = sys.argv[1:] if arguments is None else arguments
        require(arguments in (["--check"], ["--execute"]), "explicit_mode_required")
        hashes = file_hashes()
        with connect_verified() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            _, _, present = preflight(connection)
            snapshot = preservation_snapshot(connection)
        require(file_hashes() == hashes, "source_files_or_configuration_changed")
        print(json.dumps({"check": "PASS", "database": "tanglaw_buhay_dev", "schema": "public",
                          "batch_id": 1, "historical_run_id": 1, "new_version": VERSION,
                          "selection_migration_present": present}), flush=True)
        if arguments == ["--check"]:
            return 0
        result = execute_once(snapshot, hashes)
        result["elapsed_seconds"] = round(perf_counter() - started, 3)
        print(json.dumps(result), flush=True)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print(json.dumps({"execution": "FAIL_STOPPED_NO_RETRY", "phase": PHASE,
                          "safe_error_code": code, "writes_committed": COMMITTED}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
