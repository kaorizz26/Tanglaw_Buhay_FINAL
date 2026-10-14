"""Read stored active-batch findings internally; route authorization is separate."""

from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus

from db import get_connection
from repositories import assessment_runs, dataset_batches


class AssessmentQueryError(RuntimeError):
    """Fixed failure code without household values, credentials or database details."""


def _resolve_context(connection):
    """Require one validated active batch and its explicitly designated completed run."""
    batches = dataset_batches.find_active_batches(connection)
    if not batches:
        raise AssessmentQueryError("active_batch_missing")
    if len(batches) != 1:
        raise AssessmentQueryError("active_batch_ambiguous")
    batch = batches[0]
    if batch["validation_status"] != "validated" or batch["is_active"] is not True:
        raise AssessmentQueryError("active_batch_invalid")
    run = assessment_runs.find_designated_run(connection, batch["id"])
    if run is None:
        raise AssessmentQueryError("approved_assessment_run_missing")
    if run["batch_id"] != batch["id"] or run["completed_at"] is None:
        raise AssessmentQueryError("completed_assessment_run_invalid")
    return {**run, "survey_year": batch["survey_year"], "municipality": batch["municipality"],
            "source_provenance": batch["source_provenance"]}


@contextmanager
def _read_context(connection_factory):
    """Own one fresh connection and stable read-only snapshot per service operation."""
    try:
        with connection_factory() as connection:
            if connection.info.transaction_status != TransactionStatus.IDLE:
                raise AssessmentQueryError("assessment_connection_not_idle")
            with connection.transaction():
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                yield connection, _resolve_context(connection)
    except AssessmentQueryError:
        raise
    except (psycopg.Error, RuntimeError):
        raise AssessmentQueryError("assessment_query_unavailable") from None


def get_active_assessment_context(*, connection_factory=get_connection):
    """Return source/run provenance and historical year without loading findings."""
    with _read_context(connection_factory) as (_, context):
        return context


def list_active_assessment_summaries(*, page=1, page_size=25, connection_factory=get_connection):
    """Return a bounded page ordered by stored rank/UUID, plus source/run context."""
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise AssessmentQueryError("invalid_assessment_pagination")
    with _read_context(connection_factory) as (connection, context):
        items = assessment_runs.list_assessment_summaries(
            connection, context["run_id"], limit=page_size, offset=(page - 1) * page_size,
        )
        return {"context": context, "items": items, "page": page, "page_size": page_size,
                "total": context["household_count"]}


def get_active_household_assessment(household_uuid, *, connection_factory=get_connection):
    """Return one complete stored finding with its active source/run context."""
    if not isinstance(household_uuid, str) or not household_uuid.strip():
        raise AssessmentQueryError("invalid_household_uuid")
    with _read_context(connection_factory) as (connection, context):
        findings = assessment_runs.find_household_assessment(
            connection, context["run_id"], household_uuid,
        )
        if findings is None:
            raise AssessmentQueryError("household_assessment_missing")
        return {"context": context, "findings": findings}
