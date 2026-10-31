"""Read stored active-batch findings internally; route authorization is separate."""

from contextlib import contextmanager
from dataclasses import replace

import psycopg
from psycopg.pq import TransactionStatus

from db import get_connection
from repositories import assessment_runs, dataset_batches
from repositories import assessment_evidence, staff_reviews
from services.assessment_filters import (
    AssessmentFilters, InvalidAssessmentFilters, normalize_filters,
)
from services import read_cache
from services.dashboard_patterns import present_indicator_patterns
from services.assessment_reference_labels import code_key
from services.subsidy_priority import derive_subsidy_priority
from services.spisc_age_progression import current_spisc_status, philippine_assessment_date


DASHBOARD_CACHE_SECONDS = 1800
PRIORITIZATION_CACHE_SECONDS = 300
# Priority groups whose card count uses the same rule as the page filter, so the
# card count can serve as the page total. "other" is a negated rule (a missing
# score would be counted by the cards but not matched by the page), so it is
# always counted with the page's own query.
COUNTED_PRIORITIES = ("all", "primary", "secondary", "both")


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


def _present_household_profile(raw):
    """Label the three most common water sources and keep unclear answers visible."""
    total = raw["household_count"]
    labels = {}
    for row in raw["water_labels"]:
        code = code_key(row["code"])
        label = (row["label"] or "").strip()
        if code and label:
            labels.setdefault(code, set()).add(label)

    recorded_by_code = {}
    unclear = 0
    for row in raw["water_sources"]:
        code = code_key(row["code"])
        meanings = labels.get(code, set())
        if len(meanings) == 1:
            item = recorded_by_code.setdefault(
                code, {"label": next(iter(meanings)), "count": 0},
            )
            item["count"] += row["households"]
        else:
            unclear += row["households"]
    recorded = list(recorded_by_code.values())
    recorded.sort(key=lambda item: (-item["count"], item["label"]))
    slices = recorded[:3]
    other = sum(item["count"] for item in recorded[3:])
    if other:
        slices.append({"label": "Other recorded sources", "count": other})
    if unclear:
        slices.append({"label": "Not recorded or unclear", "count": unclear})

    colors = ("#244b65", "#b78d42", "#438585", "#8299a8", "#646f78")
    position = 0
    gradient = []
    for index, item in enumerate(slices):
        next_position = position + item["count"]
        item["color"] = colors[index]
        item["percent"] = round(item["count"] / total * 100, 1) if total else 0
        gradient.append(
            f"{colors[index]} {position / total * 100:.4f}% "
            f"{next_position / total * 100:.4f}%"
        )
        position = next_position

    return {
        "average_members": round(raw["member_count"] / total, 2) if total else 0,
        "with_children": raw["households_with_children"],
        "with_older_adults": raw["households_with_older_adults"],
        "with_unclear_age": raw["households_with_unclear_age"],
        "without_electricity": raw["without_electricity"],
        "electricity_unclear": raw["electricity_unclear"],
        "shared_toilet": raw["shared_toilet"],
        "toilet_sharing_unclear": raw["toilet_sharing_unclear"],
        "water_sources": slices,
        "water_gradient": "conic-gradient(" + ", ".join(gradient) + ")"
                          if gradient else "conic-gradient(#e8edf0 0% 100%)",
    }


def _cache_scope(connection, context, assessed):
    """Identify one dataset snapshot for cache keys: database, batch, run and date.

    A new batch, a new designated run, a new completion time or a new Philippine
    date produces a different scope, so older results are never reused.
    """
    return (connection.info.dbname, context["batch_id"], context["run_id"],
            context["completed_at"], assessed)


def _current_age_statuses(connection, context, assessed):
    """Return the current-age SPISC status of each recorded age, reused for a few minutes."""
    key = ("spisc-ages", *_cache_scope(connection, context, assessed))
    return read_cache.cached(
        key, PRIORITIZATION_CACHE_SECONDS,
        lambda: _read_current_age_statuses(connection, context, assessed),
    )


def _read_current_age_statuses(connection, context, assessed):
    """Classify each recorded age once for the current Philippine date."""
    return {
        "spisc_age_statuses": {
            age: current_spisc_status([age], context, assessed)
            for age in assessment_evidence.list_recorded_ages(connection, context["batch_id"])
            if age is not None
        },
        "batch_id": context["batch_id"],
    }


def get_active_dashboard_summary(
    *, barangay=None, connection_factory=get_connection, assessment_date=None,
):
    """Read only aggregates from the designated completed run.

    An optional recorded barangay narrows the aggregates to that barangay.
    SPISC counts use the same current-age logic as Household Prioritization.
    Results are reused for a few minutes for the same database, batch, run,
    barangay and Philippine date; anything different is always read afresh.
    """
    barangay = normalize_filters(AssessmentFilters(barangay=barangay or "")).barangay
    assessed = assessment_date if assessment_date is not None else philippine_assessment_date()
    with _read_context(connection_factory) as (connection, context):
        barangays = dataset_batches.list_barangays(connection, context["batch_id"])
        if barangay and barangay not in barangays:
            raise InvalidAssessmentFilters("invalid_dashboard_barangay")
        key = ("dashboard", connection.info.dbname, context["batch_id"],
               context["run_id"], context["completed_at"], assessed, barangay)
        summary = read_cache.cached(
            key, DASHBOARD_CACHE_SECONDS,
            lambda: _read_dashboard_summary(connection, context, assessed, barangay),
        )
    return {**summary, "barangay": barangay, "barangays": barangays}


def _read_dashboard_summary(connection, context, assessed, barangay):
    filters = AssessmentFilters(barangay=barangay)
    current_age = _current_age_statuses(connection, context, assessed)
    overview = assessment_runs.count_dashboard_overview(
        connection, context["run_id"], filters=filters, **current_age,
    )
    priority, findings = overview["priority"], overview["findings"]
    household_profile = dataset_batches.count_household_profile(
        connection, context["batch_id"], barangay or None,
    )
    households = household_profile["household_count"]
    try:
        patterns = present_indicator_patterns(
            assessment_runs.count_indicator_findings(
                connection, context["run_id"], filters=filters,
            ),
            households,
        )
    except (KeyError, ValueError):
        raise AssessmentQueryError("assessment_count_mismatch") from None
    if priority["all"] != households or findings["assessed_households"] != households or (
        sum(row["households"] for row in household_profile["water_sources"]) != households
    ) or (not barangay and households != context["household_count"]):
        raise AssessmentQueryError("assessment_count_mismatch")
    return {"context": context, "priority": priority, "findings": findings,
            "indicator_patterns": patterns,
            "household_profile": _present_household_profile(household_profile)}


def list_active_assessment_summaries(
    *, page=1, page_size=25, include_household_identity=False,
    filters=None, include_barangays=False, include_priority_counts=False,
    connection_factory=get_connection, assessment_date=None,
):
    """Return a bounded rank/UUID page and optional identities in one snapshot.

    Filters/counts and optional active-batch barangays share the selected-run
    snapshot. Identities read only the current page's household/head fields.
    Callers remain responsible for authenticated staff authorization.
    An identical request for the same dataset snapshot and Philippine date is
    answered from memory for a few minutes; anything different is read afresh.
    """
    if (
        type(page) is not int or page < 1
        or type(page_size) is not int or not 1 <= page_size <= 100
    ):
        raise AssessmentQueryError("invalid_assessment_pagination")
    filters = normalize_filters(filters)
    assessed = assessment_date if assessment_date is not None else philippine_assessment_date()
    with _read_context(connection_factory) as (connection, context):
        # The caller has already been authorized. The key includes every input
        # that changes the page, so only an identical request reuses a result.
        key = ("prioritization-page", *_cache_scope(connection, context, assessed),
               page, page_size, filters, include_household_identity,
               include_barangays, include_priority_counts)
        return read_cache.cached(
            key, PRIORITIZATION_CACHE_SECONDS,
            lambda: _read_summary_page(
                connection, context, assessed, page, page_size, filters,
                include_household_identity, include_barangays, include_priority_counts,
            ),
        )


def _priority_counts(connection, context, assessed, filters, current_age):
    """Count the four priority cards, reused while only the selected card changes.

    The counts ignore the selected priority but respect every other filter, so
    the key uses the filters with the priority reset to "all".
    """
    key = ("priority-counts", *_cache_scope(connection, context, assessed),
           replace(filters, priority="all"))
    return read_cache.cached(
        key, PRIORITIZATION_CACHE_SECONDS,
        lambda: assessment_runs.count_priority_groups(
            connection, context["run_id"], filters=filters, **current_age,
        ),
    )


def _read_summary_page(
    connection, context, assessed, page, page_size, filters,
    include_household_identity, include_barangays, include_priority_counts,
):
    """Read one summary page from the database; the caller decides whether to cache it."""
    current_age = _current_age_statuses(connection, context, assessed)
    selection = {"filters": filters} if filters.active else {}
    total = context["household_count"]
    priority_counts = None
    if include_priority_counts:
        priority_counts = _priority_counts(connection, context, assessed, filters, current_age)
    if filters.active:
        if priority_counts is not None and filters.priority in COUNTED_PRIORITIES:
            total = priority_counts[filters.priority]
        else:
            total = assessment_runs.count_assessment_summaries(
                connection, context["run_id"], filters=filters, **current_age,
            )
    items = assessment_runs.list_assessment_summaries(
        connection, context["run_id"], limit=page_size, offset=(page - 1) * page_size,
        **selection, **current_age,
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
        "assessment_date": assessed,
    }
    if include_barangays:
        result["barangays"] = dataset_batches.list_barangays(connection, context["batch_id"])
    if priority_counts is not None:
        result["priority_counts"] = priority_counts
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


def get_active_assessment_detail(
    household_uuid, *, include_staff_reviews=False, connection_factory=get_connection,
):
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
        result = {
            "context": context, "findings": findings, "identity": identities[0],
            "household": household,
            "members": assessment_evidence.list_member_sources(
                connection, context["batch_id"], household_uuid,
            ),
            "references": assessment_evidence.list_evidence_references(
                connection, context["batch_id"],
            ),
        }
        if include_staff_reviews:
            result["staff_reviews"] = staff_reviews.list_history(
                connection, context["batch_id"], household_uuid,
            )
        return result
