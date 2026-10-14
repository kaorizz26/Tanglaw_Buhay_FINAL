"""Explicit one-time pilot persistence; private comparisons remain only in memory."""

from hashlib import sha256
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg import sql
from psycopg.rows import dict_row

from repositories import assessment_runs as repository
from scripts.controlled_cbms_import_2022 import (
    EXPECTED, TABLES, connect_verified, emit, file_hashes, staff_fingerprint,
    table_counts, verify_schema,
)
from scripts.verify_active_batch_assessments import (
    VerificationError, process_memory, require, source_fingerprint, verify_outputs,
)
from services.active_batch_loader import _batch_metadata, load_active_batch
from services.assessments import combine_assessments
from services.subsidy_screening import screen_households
from services.vulnerability.scoring import assess_households


MIGRATION = ROOT / "migrations/0003_assessment_persistence.sql"
CALCULATION_VERSION = "tanglaw-buhay-approved-mpi-screening-v1"
ASSESSMENT_COLUMNS = {
    "assessment_runs": ("id", "batch_id", "calculation_version", "calculation_provenance",
                        "expected_household_count", "status", "created_at", "completed_at"),
    "household_assessments": ("run_id", "batch_id", "household_uuid", "findings",
                              "vulnerability_rank"),
}
BASELINE_STATUSES = {
    "4Ps": (12147, 7150, 7), "SLP": (12770, 2385, 4149),
    "SPISC": (6339, 12965, 0), "TUPAD": (16612, 2692, 0),
}
PHASE = "configuration"
COMMITTED = False


def assessment_schema_present(connection):
    """Reject partial or unexpected assessment structures instead of repairing them."""
    present = connection.execute(
        "SELECT to_regclass('public.assessment_runs') IS NOT NULL, "
        "to_regclass('public.household_assessments') IS NOT NULL",
    ).fetchone()
    require(present in ((False, False), (True, True)), "partial_assessment_schema")
    if not present[0]:
        return False
    for table, expected in ASSESSMENT_COLUMNS.items():
        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,),
        ).fetchall()
        require(tuple(row[0] for row in columns) == expected, "assessment_schema_mismatch")
    constraints = connection.execute(
        "SELECT con.contype, count(*), bool_and(con.convalidated) FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace "
        "WHERE ns.nspname='public' AND rel.relname=ANY(%s) GROUP BY con.contype",
        (list(ASSESSMENT_COLUMNS),),
    ).fetchall()
    catalog = {kind: (count, validated) for kind, count, validated in constraints}
    require(catalog.get("p") == (2, True) and catalog.get("f") == (3, True)
            and catalog.get("u") == (1, True), "assessment_constraints_mismatch")
    generated = connection.execute(
        "SELECT is_generated FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='household_assessments' AND column_name='vulnerability_rank'",
    ).fetchone()
    require(generated == ("ALWAYS",), "assessment_rank_projection_missing")
    indexes = connection.execute(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relname IN ('assessment_runs_batch', 'household_assessments_rank') AND i.indisvalid",
    ).fetchone()[0]
    require(indexes == 2, "assessment_indexes_missing")
    return True


def preservation_snapshot(connection):
    """Fingerprint every source/metadata row and staff state without exposing values."""
    fingerprints = {}
    for table in ("dataset_batches", *TABLES.values()):
        fingerprints[table] = connection.execute(sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(digest, '' ORDER BY digest), '')) "
            "FROM (SELECT md5(to_jsonb(s)::text) AS digest FROM public.{} s) hashes"
        ).format(sql.Identifier(table))).fetchone()
    return {"sources": fingerprints, "staff": staff_fingerprint(connection)}


def preflight(connection):
    """Read-only identity/schema/batch/count/repeat safeguards for the fixed pilot."""
    verify_schema(connection)
    with connection.cursor(row_factory=dict_row) as cursor:
        batches = cursor.execute("SELECT * FROM public.dataset_batches WHERE is_active").fetchall()
    metadata = _batch_metadata(batches)
    require(metadata["batch_id"] == 1 and metadata["survey_year"] == 2022
            and metadata["municipality"] == "Pozorrubio", "unexpected_active_pilot_batch")
    require(table_counts(connection) == EXPECTED, "unexpected_source_counts")
    present = assessment_schema_present(connection)
    if present:
        counts = connection.execute(
            "SELECT (SELECT count(*) FROM public.assessment_runs WHERE batch_id=1), "
            "(SELECT count(*) FROM public.household_assessments WHERE batch_id=1)",
        ).fetchone()
        require(counts == (0, 0), "existing_pilot_assessment_stop")
    snapshot = preservation_snapshot(connection)
    require(snapshot["staff"][2] >= 1, "existing_admin_missing")
    return metadata, present, snapshot


def verify_baseline(summary):
    """Compare verified aggregates with Task 2A; never introduce calculation rules."""
    mpi, screening = summary["mpi"], summary["screening"]
    require(summary["unified_households"] == 19304 and mpi["households"] == 19304
            and mpi["indicators"] == 16 and screening["results"] == 77216
            and screening["survey_year"] == 2022, "baseline_coverage_mismatch")
    require(mpi["data_quality_counts"] == {"Complete Data": 19093, "Needs Data Review": 211}
            and mpi["unknown_indicator_events"] == 212, "baseline_unknown_counts_mismatch")
    require(mpi["ranks"] == {"ranked_households": 19304, "min": 1, "max": 14366,
                            "distinct_scores": 37, "tied_score_groups": 35,
                            "households_in_ties": 19302}, "baseline_rank_mismatch")
    for program, counts in BASELINE_STATUSES.items():
        expected = dict(zip(("For Review", "No Historical CBMS Trigger", "Needs Data Review"), counts))
        require(screening["program_status_counts"][program] == expected, "baseline_status_mismatch")
    expected_scores = {"min": 0, "p25": 0, "median": 5.5556, "p75": 12.5,
                       "mean": 8.3175, "max": 56.9444}
    require(all(abs(mpi["score_distribution"][key] - value) <= 0.00005
                for key, value in expected_scores.items()), "baseline_scores_mismatch")
    require(abs(mpi["coverage_pct"]["mean"] - 99.8981932357) < 1e-8,
            "baseline_indicator_coverage_mismatch")
    require(list(mpi["score_histogram"].values()) == [12502, 4682, 1623, 406, 83, 8, 0, 0, 0, 0],
            "baseline_histogram_mismatch")


def calculate_once(metadata):
    """Load read-only, execute each unchanged engine once and verify its outputs."""
    datasets, references, loaded = load_active_batch(connection_factory=connect_verified)
    require(loaded == metadata, "source_batch_metadata_changed")
    fingerprint = source_fingerprint(datasets, references)
    emit({"progress": "calculate_and_verify", "households": 19304, "programs": 4})
    mpi = assess_households(datasets, references["mpi_mapping"])
    screening = screen_households(datasets, survey_year=metadata["survey_year"])
    summary = verify_outputs(datasets, mpi, screening, metadata["survey_year"])
    verify_baseline(summary)
    require(source_fingerprint(datasets, references) == fingerprint, "source_frames_changed")
    return combine_assessments(mpi, screening, survey_year=metadata["survey_year"]), summary


def verify_stored(connection, run_id, original):
    """Read every completed finding and compare all values, evidence and original ranks."""
    run = repository.find_run(connection, run_id)
    require(run is not None and run["batch_id"] == 1 and run["survey_year"] == 2022
            and run["municipality"] == "Pozorrubio" and run["status"] == "completed"
            and run["expected_household_count"] == len(original)
            and run["calculation_version"] == CALCULATION_VERSION, "stored_run_metadata_mismatch")
    seen, previous = set(), None
    for finding in repository.iter_household_assessments(connection, run_id):
        uuid = finding["household_uuid"]
        require(uuid in original and uuid not in seen and finding == original[uuid],
                "stored_complete_findings_mismatch")
        position = (finding["mpi"]["vulnerability_rank"], uuid)
        require(previous is None or position >= previous, "stored_rank_order_mismatch")
        previous = position
        seen.add(uuid)
    require(seen == set(original), "stored_household_coverage_mismatch")
    counts = connection.execute(
        "SELECT (SELECT count(*) FROM public.assessment_runs WHERE batch_id=1), "
        "(SELECT count(*) FROM public.assessment_runs WHERE batch_id=1 AND status='completed'), "
        "(SELECT count(*) FROM public.household_assessments WHERE batch_id=1), "
        "(SELECT count(*) FROM public.household_assessments h, "
        "LATERAL jsonb_each(h.findings->'programs') p WHERE h.batch_id=1)",
    ).fetchone()
    require(counts == (1, 1, len(original), 4 * len(original)), "stored_assessment_counts_mismatch")
    return {"completed_runs": counts[1], "households": counts[2], "subsidy_findings": counts[3]}


def calculation_provenance(metadata):
    """Record actual source provenance and implementation hashes without private findings."""
    paths = [ROOT / "services/assessments.py", ROOT / "services/subsidy_screening.py",
             *sorted((ROOT / "services/vulnerability").glob("*.py")),
             *sorted((ROOT / "services/screening").glob("*.py"))]
    return {"purpose": "Owner-authorized historical decision support; no eligibility decisions",
            "authorization": "Phase 6B.4 Task 3B, one controlled local pilot persistence",
            "source_batch_provenance": metadata["source_provenance"],
            "engine_sha256": {path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
                              for path in paths},
            "migration_sha256": sha256(MIGRATION.read_bytes()).hexdigest(),
            "verification_baseline": "Phase 6B.4 Task 2A and Task 2B; approved rules unchanged"}


def execute_once(expected_snapshot, expected_hashes):
    """Commit migration/results together only after verification; propagate failures once."""
    global PHASE, COMMITTED
    with connect_verified() as connection:
        with connection.transaction():
            # Identity was verified read-only before enabling this single transaction.
            connection.execute("SET TRANSACTION READ WRITE")
            connection.execute("SELECT pg_advisory_xact_lock(6042022)")
            for table in ("dataset_batches", *TABLES.values(), "staff_users"):
                connection.execute(sql.SQL("LOCK TABLE public.{} IN SHARE MODE").format(
                    sql.Identifier(table)))
            if assessment_schema_present(connection):
                connection.execute("LOCK TABLE public.assessment_runs, "
                                   "public.household_assessments IN SHARE ROW EXCLUSIVE MODE")
            metadata, present, snapshot = preflight(connection)
            require(snapshot == expected_snapshot, "source_or_staff_changed_before_execution")
            require(file_hashes() == expected_hashes, "source_or_configuration_changed")
            PHASE = "atomic_migration_and_assessments"
            if not present:
                connection.execute(MIGRATION.read_text(encoding="utf-8"))
            require(assessment_schema_present(connection), "assessment_migration_missing")
            original, summary = calculate_once(metadata)
            provenance = calculation_provenance(metadata)
            run_id = repository.create_run(connection, 1, calculation_version=CALCULATION_VERSION,
                                           calculation_provenance=provenance)
            repository.store_household_assessments(connection, run_id, original)
            repository.complete_run(connection, run_id)
            stored = verify_stored(connection, run_id, original)
            require(repository.find_run(connection, run_id)["calculation_provenance"] == provenance,
                    "stored_calculation_provenance_mismatch")
            require(preservation_snapshot(connection) == snapshot, "source_or_staff_changed")
            require(file_hashes() == expected_hashes, "source_or_configuration_changed")
        # A fresh guarded read-only connection verifies the committed state without recalculation.
        COMMITTED = True
    PHASE = "read_only_post_commit_verification"
    with connect_verified() as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        require(assessment_schema_present(connection), "committed_migration_missing")
        require(verify_stored(connection, run_id, original) == stored, "post_commit_counts_mismatch")
        require(repository.find_run(connection, run_id)["calculation_provenance"] == provenance,
                "committed_calculation_provenance_mismatch")
        require(preservation_snapshot(connection) == snapshot, "post_commit_source_or_staff_changed")
    require(file_hashes() == expected_hashes, "post_commit_source_or_configuration_changed")
    return {"execution": "SUCCESS", "migration_0003": "already_present" if present else "applied",
            "database": "tanglaw_buhay_dev", "schema": "public", "run_id": run_id, "batch_id": 1,
            "stored": stored, "summary": summary, "complete_findings_and_ranks_preserved": True,
            "source_records_metadata_and_staff_unchanged": True,
            "seven_csv_files_and_private_env_unchanged": True}


def main(arguments=None):
    """Require an explicit mode; emit only aggregate success or sanitized failure."""
    global PHASE, COMMITTED
    PHASE, COMMITTED = "configuration", False
    started = perf_counter()
    try:
        arguments = sys.argv[1:] if arguments is None else arguments
        require(arguments in (["--check"], ["--execute"]), "explicit_mode_required")
        hashes = file_hashes()
        PHASE = "read_only_preflight"
        with connect_verified() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            _, present, snapshot = preflight(connection)
        require(file_hashes() == hashes, "source_or_configuration_changed")
        emit({"check": "PASS", "database": "tanglaw_buhay_dev", "schema": "public",
              "batch_id": 1, "households": 19304, "existing_pilot_runs": 0,
              "migration_0003": "present" if present else "not_yet_applied"})
        if arguments == ["--check"]:
            return 0
        result = execute_once(snapshot, hashes)
        result.update(elapsed_seconds=round(perf_counter() - started, 3),
                      process_memory=process_memory())
        emit(result)
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        emit({"execution": "FAIL_STOPPED_NO_RETRY", "phase": PHASE, "safe_error_code": code,
              "writes_committed": COMMITTED,
              "details": "Private failure details suppressed. No automatic repairs or retries."})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
