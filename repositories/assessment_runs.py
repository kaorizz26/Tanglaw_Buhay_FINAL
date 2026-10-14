"""Batch-scoped assessment storage; callers own transactions and authorization."""

from copy import deepcopy
import json
from numbers import Integral, Real

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.screening.metadata import OUTPUT_COLUMNS, PROGRAMS, STATUSES
from services.vulnerability.scoring import INDICATOR_WEIGHTS, METRIC_COLUMNS


MPI_TUPLES = ("assumptions_used", "unknown_zero_point_indicators")
PROGRAM_TUPLES = (
    "trigger_reasons", "supporting_evidence", "triggering_member_line_numbers",
    "key_unverified_requirements", "source_references", "methodology_notes",
    "data_quality_warnings",
)
MPI_AUDIT_FIELDS = (
    "e01_assumed_not_working_count", "ofw_assumed_working_count", "b06_e01_conflict_count",
)


class AssessmentStorageError(RuntimeError):
    """Fixed storage-validation failure without private payload values."""


def _restore_findings(value):
    """Restore the existing unified output's tuple fields and program order."""
    result = deepcopy(value)
    for field in MPI_TUPLES:
        result["mpi"][field] = tuple(result["mpi"][field])
    result["programs"] = {code: result["programs"][code] for code in PROGRAMS}
    for program in result["programs"].values():
        for field in PROGRAM_TUPLES:
            program[field] = tuple(program[field])
    return result


def _serialize_findings(findings, household_uuid, survey_year):
    """Require complete existing output structures; never recalculate findings."""
    try:
        mpi, programs = findings["mpi"], findings["programs"]
        required_mpi = {"uuid", *INDICATOR_WEIGHTS, *METRIC_COLUMNS, *MPI_AUDIT_FIELDS}
        valid = (
            isinstance(household_uuid, str) and bool(household_uuid.strip())
            and findings["household_uuid"] == household_uuid
            and type(findings["survey_year"]) is int
            and findings["survey_year"] == survey_year
            and isinstance(mpi, dict) and required_mpi.issubset(mpi)
            and mpi["uuid"] == household_uuid
            and all(mpi[name] is None or (isinstance(mpi[name], Integral)
                    and not isinstance(mpi[name], bool) and mpi[name] in (0, 1))
                    for name in INDICATOR_WEIGHTS)
            and isinstance(mpi["vulnerability_rank"], Integral)
            and not isinstance(mpi["vulnerability_rank"], bool)
            and mpi["vulnerability_rank"] > 0
            and isinstance(mpi["vulnerability_score"], Real)
            and not isinstance(mpi["vulnerability_score"], bool)
            and 0 <= mpi["vulnerability_score"] <= 100
            and mpi["data_quality_status"] in ("Complete Data", "Needs Data Review")
            and all(isinstance(mpi[field], tuple) for field in MPI_TUPLES)
            and isinstance(programs, dict) and set(programs) == set(PROGRAMS)
        )
        if not valid:
            raise AssessmentStorageError("invalid_assessment_findings")
        for code, program in programs.items():
            if not (
                isinstance(program, dict) and set(OUTPUT_COLUMNS).issubset(program)
                and program["household_uuid"] == household_uuid
                and program["program_code"] == code
                and type(program["survey_year"]) is int
                and program["survey_year"] == survey_year
                and program["screening_status"] in STATUSES
                and all(isinstance(program[field], tuple) for field in PROGRAM_TUPLES)
            ):
                raise AssessmentStorageError("invalid_assessment_findings")
        encoded = json.loads(json.dumps(findings, allow_nan=False))
        # Reject unsupported new sequence structures instead of silently losing them.
        if _restore_findings(encoded) != findings:
            raise AssessmentStorageError("unsupported_assessment_structure")
        return encoded
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AssessmentStorageError("invalid_assessment_findings") from None


def create_run(connection, batch_id, *, calculation_version, calculation_provenance):
    """Create a pending run for a validated batch and snapshot its household count."""
    if not isinstance(calculation_version, str) or not calculation_version.strip():
        raise AssessmentStorageError("invalid_calculation_metadata")
    if not isinstance(calculation_provenance, dict) or not calculation_provenance:
        raise AssessmentStorageError("invalid_calculation_metadata")
    try:
        json.dumps(calculation_provenance, allow_nan=False)
    except (TypeError, ValueError):
        raise AssessmentStorageError("invalid_calculation_metadata") from None
    row = connection.execute(
        """INSERT INTO assessment_runs
           (batch_id, calculation_version, calculation_provenance, expected_household_count)
           SELECT b.id, %s, %s, count(h.uuid)
           FROM dataset_batches b JOIN households h ON h.batch_id = b.id
           WHERE b.id = %s AND b.validation_status = 'validated'
           GROUP BY b.id RETURNING id""",
        (calculation_version, Jsonb(calculation_provenance), batch_id),
    ).fetchone()
    if row is None:
        raise AssessmentStorageError("validated_source_batch_missing")
    return row[0]


def _pending_run(connection, run_id):
    """Lock the run to serialize inserts/completion and keep completed results fixed."""
    with connection.cursor(row_factory=dict_row) as cursor:
        run = cursor.execute(
            """SELECT r.batch_id, r.expected_household_count, r.status, b.survey_year
               FROM assessment_runs r JOIN dataset_batches b ON b.id = r.batch_id
               WHERE r.id = %s FOR UPDATE OF r""", (run_id,),
        ).fetchone()
    if run is None or run["status"] != "pending":
        raise AssessmentStorageError("assessment_run_not_pending")
    return run


def store_household_assessments(connection, run_id, assessments):
    """Append unified findings to a pending run; duplicates fail rather than overwrite."""
    run = _pending_run(connection, run_id)

    def parameters():
        for household_uuid, findings in assessments.items():
            payload = _serialize_findings(findings, household_uuid, run["survey_year"])
            yield run_id, run["batch_id"], household_uuid, Jsonb(payload)

    with connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings)
               VALUES (%s, %s, %s, %s)""", parameters(),
        )


def complete_run(connection, run_id):
    """Finalize only after exactly one assessment exists for every source household."""
    run = _pending_run(connection, run_id)
    stored, source = connection.execute(
        """SELECT (SELECT count(*) FROM household_assessments WHERE run_id = %s),
                  (SELECT count(*) FROM households WHERE batch_id = %s)""",
        (run_id, run["batch_id"]),
    ).fetchone()
    if stored != run["expected_household_count"] or source != stored:
        raise AssessmentStorageError("incomplete_assessment_run")
    connection.execute(
        """UPDATE assessment_runs SET status = 'completed', completed_at = CURRENT_TIMESTAMP
           WHERE id = %s AND status = 'pending'""", (run_id,),
    )


def find_run(connection, run_id):
    """Return technical run/source metadata; pending runs contain no exposed findings."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT r.*, b.survey_year, b.municipality, b.source_provenance
               FROM assessment_runs r JOIN dataset_batches b ON b.id = r.batch_id
               WHERE r.id = %s""", (run_id,),
        ).fetchone()


def find_household_assessment(connection, run_id, household_uuid):
    """Return complete original findings only from an explicitly selected completed run."""
    row = connection.execute(
        """SELECT h.findings FROM household_assessments h
           JOIN assessment_runs r ON r.id = h.run_id AND r.batch_id = h.batch_id
           WHERE h.run_id = %s AND h.household_uuid = %s AND r.status = 'completed'""",
        (run_id, household_uuid),
    ).fetchone()
    return _restore_findings(row[0]) if row is not None else None


def iter_household_assessments(connection, run_id):
    """Yield completed-run findings by original rank, preserving ties and all evidence."""
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT h.findings FROM household_assessments h
               JOIN assessment_runs r ON r.id = h.run_id AND r.batch_id = h.batch_id
               WHERE h.run_id = %s AND r.status = 'completed'
               ORDER BY h.vulnerability_rank ASC, h.household_uuid ASC""", (run_id,),
        )
        for (findings,) in cursor:
            yield _restore_findings(findings)
