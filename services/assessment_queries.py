"""Read stored active-batch findings internally; route authorization is separate."""

from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus

from db import get_connection
from repositories import assessment_runs, dataset_batches
from repositories import assessment_evidence
from services.assessment_filters import normalize_filters
from services.subsidy_priority import derive_subsidy_priority


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


def list_active_assessment_summaries(
    *, page=1, page_size=25, include_household_identity=False,
    filters=None, include_barangays=False,
    connection_factory=get_connection,
):
    """Return a bounded rank/UUID page and optional identities in one snapshot.

    Filters/counts and optional active-batch barangays share the selected-run
    snapshot. Identities read only the current page's household/head fields.
    Callers remain responsible for authenticated staff authorization.
    """
    if (
        type(page) is not int or page < 1
        or type(page_size) is not int or not 1 <= page_size <= 100
    ):
        raise AssessmentQueryError("invalid_assessment_pagination")
    filters = normalize_filters(filters)
    with _read_context(connection_factory) as (connection, context):
        selection = {"filters": filters} if filters.active else {}
        total = context["household_count"]
        if filters.active:
            total = assessment_runs.count_assessment_summaries(
                connection, context["run_id"], filters=filters,
            )
        items = assessment_runs.list_assessment_summaries(
            connection, context["run_id"], limit=page_size, offset=(page - 1) * page_size,
            **selection,
        )
        items = [
            {**item, "subsidy_priority": derive_subsidy_priority(
                item["vulnerability_score"], item["program_statuses"],
            )}
            for item in items
        ]
        if include_household_identity:
            identities = dataset_batches.find_household_identities(
                connection, context["batch_id"],
                [item["household_uuid"] for item in items],
            )
            by_uuid = {item["household_uuid"]: item for item in identities}
            if set(by_uuid) != {item["household_uuid"] for item in items}:
                raise AssessmentQueryError("household_identity_missing")
            items = [
                {**item, "identity": by_uuid[item["household_uuid"]]}
                for item in items
            ]
        result = {
            "context": context,
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }
        if include_barangays:
            result["barangays"] = dataset_batches.list_barangays(connection, context["batch_id"])
        return result


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


def get_active_assessment_detail(household_uuid, *, connection_factory=get_connection):
    """Read one stored assessment and relevant sources in the same stable snapshot.

    The caller enforces staff authorization. No engine, loader or write operation
    is used; source and reference reads use the designated run's batch identity.
    """
    if (
        not isinstance(household_uuid, str) or not household_uuid.strip()
        or len(household_uuid) > 128 or "\x00" in household_uuid
    ):
        raise AssessmentQueryError("invalid_household_uuid")
    with _read_context(connection_factory) as (connection, context):
        findings = assessment_runs.find_household_assessment(
            connection, context["run_id"], household_uuid,
        )
        if findings is None:
            raise AssessmentQueryError("household_assessment_missing")
        identities = dataset_batches.find_household_identities(
            connection, context["batch_id"], [household_uuid],
        )
        household = assessment_evidence.find_household_sources(
            connection, context["batch_id"], household_uuid,
        )
        if len(identities) != 1 or household is None:
            raise AssessmentQueryError("household_identity_missing")
        return {
            "context": context, "findings": findings, "identity": identities[0],
            "household": household,
            "members": assessment_evidence.list_member_sources(
                connection, context["batch_id"], household_uuid,
            ),
            "references": assessment_evidence.list_evidence_references(
                connection, context["batch_id"],
            ),
        }
