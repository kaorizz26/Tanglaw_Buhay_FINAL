"""Prepare one inactive import with unchanged engines and verified stored findings."""

from hashlib import sha256
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from db import get_connection
from repositories import assessment_runs, dataset_batches
from scripts.verify_active_batch_assessments import (
    source_fingerprint, verify_outputs,
)
from services.active_batch_loader import ActiveBatchError, load_inactive_batch
from services.assessments import combine_assessments
from services.subsidy_screening import screen_households
from services.vulnerability.scoring import assess_households


ROOT = Path(__file__).resolve().parents[1]
PREPARATION_LOCK_KEY = 20222027
ENGINE_PATHS = (
    ROOT / "services/assessments.py",
    ROOT / "services/subsidy_screening.py",
    ROOT / "services/subsidy_priority.py",
    *sorted((ROOT / "services/vulnerability").glob("*.py")),
    *sorted((ROOT / "services/screening").glob("*.py")),
)


class AssessmentPreparationError(RuntimeError):
    """Fixed preparation failure that contains no household values."""


def list_import_batches(connection_factory=get_connection):
    """Show only administrator-staged batches and aggregate run state."""
    try:
        with connection_factory() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                return cursor.execute(
                    """SELECT b.id, b.survey_year, b.is_active,
                              count(DISTINCT h.uuid) AS households,
                              count(DISTINCT r.id) AS run_count,
                              max(s.run_id) AS designated_run_id
                       FROM dataset_batches b
                       JOIN households h ON h.batch_id = b.id
                       LEFT JOIN assessment_runs r ON r.batch_id = b.id
                       LEFT JOIN assessment_run_selections s ON s.batch_id = b.id
                       WHERE b.validation_status = 'validated'
                         AND b.source_provenance -> 'declared' ->> 'source' =
                             'administrator_uploaded_2022_compatible_csv'
                       GROUP BY b.id ORDER BY b.id DESC""",
                ).fetchall()
    except (psycopg.Error, RuntimeError):
        raise AssessmentPreparationError("batch_list_unavailable") from None


def _engine_hashes():
    return {
        path.relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in ENGINE_PATHS
    }


def _require_preparable(connection, batch_id, actor_id, source_provenance):
    """Recheck authorization, batch identity and absence of older runs under lock."""
    actor = connection.execute(
        """SELECT role, is_active FROM staff_users WHERE id = %s FOR SHARE""",
        (actor_id,),
    ).fetchone()
    batch = connection.execute(
        """SELECT is_active, validation_status, source_provenance
           FROM dataset_batches WHERE id = %s FOR UPDATE""",
        (batch_id,),
    ).fetchone()
    existing = connection.execute(
        "SELECT 1 FROM assessment_runs WHERE batch_id = %s LIMIT 1",
        (batch_id,),
    ).fetchone()
    if (actor != ("system_administrator", True) or batch is None
            or batch[0] or batch[1] != "validated"
            or batch[2] != source_provenance or existing
            or source_provenance.get("declared", {}).get("source") !=
               "administrator_uploaded_2022_compatible_csv"):
        raise AssessmentPreparationError("batch_not_preparable")


def _verify_stored(connection, batch_id, run_id, findings):
    """Compare all stored findings, evidence and ranks with calculated output."""
    run = assessment_runs.find_run(connection, run_id)
    if (run is None or run["batch_id"] != batch_id
            or run["status"] != "completed"
            or run["expected_household_count"] != len(findings)):
        raise AssessmentPreparationError("stored_run_mismatch")
    seen = set()
    previous = None
    for stored in assessment_runs.iter_household_assessments(connection, run_id):
        household_uuid = stored["household_uuid"]
        position = (stored["mpi"]["vulnerability_rank"], household_uuid)
        if (household_uuid in seen or stored != findings.get(household_uuid)
                or (previous is not None and position < previous)):
            raise AssessmentPreparationError("stored_findings_mismatch")
        seen.add(household_uuid)
        previous = position
    if seen != set(findings):
        raise AssessmentPreparationError("stored_coverage_mismatch")
    selected = assessment_runs.find_designated_run(connection, batch_id)
    if selected is None or selected["run_id"] != run_id:
        raise AssessmentPreparationError("run_designation_mismatch")


def prepare_import_batch(batch_id, actor_id, *, connection_factory=get_connection):
    """Calculate, persist, verify and designate an inactive batch atomically."""
    if (type(batch_id) is not int or batch_id <= 0
            or type(actor_id) is not int or actor_id <= 0):
        raise AssessmentPreparationError("invalid_preparation_request")
    try:
        with connection_factory() as connection:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(%s)", (PREPARATION_LOCK_KEY,),
            ).fetchone()[0]
            connection.commit()
            if not locked:
                raise AssessmentPreparationError("preparation_in_progress")
            try:
                with connection.transaction():
                    preliminary = dataset_batches.find_validated_batch_metadata(
                        connection, batch_id,
                    )
                    if preliminary is None:
                        raise AssessmentPreparationError("batch_not_preparable")
                    _require_preparable(
                        connection, batch_id, actor_id,
                        preliminary["source_provenance"],
                    )
                datasets, references, metadata = load_inactive_batch(
                    batch_id, connection_factory=connection_factory,
                )
                fingerprint = source_fingerprint(datasets, references)
                engine_hashes = _engine_hashes()
                mpi = assess_households(datasets, references["mpi_mapping"])
                screening = screen_households(
                    datasets, survey_year=metadata["survey_year"],
                )
                verify_outputs(datasets, mpi, screening, metadata["survey_year"])
                findings = combine_assessments(
                    mpi, screening, survey_year=metadata["survey_year"],
                )
                if (source_fingerprint(datasets, references) != fingerprint
                        or _engine_hashes() != engine_hashes):
                    raise AssessmentPreparationError("preparation_inputs_changed")
                version_digest = sha256(
                    repr(sorted(engine_hashes.items())).encode("ascii"),
                ).hexdigest()
                provenance = {
                    "purpose": "historical decision support",
                    "prepared_by_staff_id": actor_id,
                    "source_bundle_sha256": metadata["source_provenance"]["declared"]["bundle_sha256"],
                    "reference_version": metadata["source_provenance"]["declared"]["reference_version"],
                    "engine_sha256": engine_hashes,
                }
                with connection.transaction():
                    _require_preparable(
                        connection, batch_id, actor_id,
                        metadata["source_provenance"],
                    )
                    dataset_batches.verify_batch_counts(
                        connection, batch_id, datasets, references,
                    )
                    run_id = assessment_runs.create_run(
                        connection, batch_id,
                        calculation_version=f"tanglaw-buhay-import-{version_digest[:16]}",
                        calculation_provenance=provenance,
                    )
                    assessment_runs.store_household_assessments(
                        connection, run_id, findings,
                    )
                    assessment_runs.complete_run(connection, run_id)
                    assessment_runs.designate_run(connection, batch_id, run_id)
                    _verify_stored(connection, batch_id, run_id, findings)
                return {"batch_id": batch_id, "run_id": run_id,
                        "households": len(findings),
                        "subsidy_findings": 4 * len(findings)}
            finally:
                connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (PREPARATION_LOCK_KEY,),
                )
                connection.commit()
    except AssessmentPreparationError:
        raise
    except (ActiveBatchError, assessment_runs.AssessmentStorageError,
            psycopg.Error, OSError, RuntimeError, ValueError, KeyError,
            TypeError):
        raise AssessmentPreparationError("preparation_unavailable") from None
