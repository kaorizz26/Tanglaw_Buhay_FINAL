"""Batch-scoped assessment storage; callers own transactions and authorization."""

from copy import deepcopy
from dataclasses import replace
import json
from numbers import Integral, Real
import re

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from services.assessment_filters import AssessmentFilters, PROGRAM_ORDER
from services.screening.metadata import OUTPUT_COLUMNS, PROGRAMS, STATUSES
from services.subsidy_priority import PRIMARY_CUTOFF, SECONDARY_MIN_SCORE
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


def find_completed_runs(connection, batch_id):
    """List all completed versions; designation remains an explicit choice."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id AS run_id, batch_id, calculation_version, calculation_provenance,
                      expected_household_count AS household_count, completed_at
               FROM assessment_runs WHERE batch_id = %s AND status = 'completed'
               ORDER BY id""", (batch_id,),
        ).fetchall()


def find_designated_run(connection, batch_id):
    """Read only the explicitly selected completed run for a validated source batch."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT r.id AS run_id, r.batch_id, r.calculation_version, r.calculation_provenance,
                      r.expected_household_count AS household_count, r.completed_at
               FROM assessment_run_selections s
               JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = s.batch_id
               JOIN dataset_batches b ON b.id = s.batch_id
               WHERE s.batch_id = %s AND r.status = 'completed'
                 AND b.validation_status = 'validated'""", (batch_id,),
        ).fetchone()


def designate_run(connection, batch_id, run_id):
    """Select a completed run explicitly; the caller owns the atomic transaction."""
    changed = connection.execute(
        """INSERT INTO assessment_run_selections (batch_id, run_id)
           SELECT r.batch_id, r.id FROM assessment_runs r
           JOIN dataset_batches b ON b.id = r.batch_id
           WHERE r.id = %s AND r.batch_id = %s AND r.status = 'completed'
             AND b.validation_status = 'validated'
           ON CONFLICT (batch_id) DO UPDATE
           SET run_id = EXCLUDED.run_id, selected_at = CURRENT_TIMESTAMP
           RETURNING run_id""", (run_id, batch_id),
    ).fetchone()
    if changed is None:
        raise AssessmentStorageError("completed_run_designation_rejected")


def _reference_position(search):
    """Recognize the existing canonical reference so ID lookup uses source indexes."""
    match = re.fullmatch(r"HH-([0-9]{5,19})", search, flags=re.IGNORECASE)
    position = int(match[1]) if match else 0
    if 0 < position <= 9223372036854775807 and search.upper() == f"HH-{position:05d}":
        return position
    return None


def _head_search(search):
    """Match literal text against the displayed name of a unique batch-scoped head."""
    pattern = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    condition = """EXISTS (
            SELECT 1 FROM household_members m
            WHERE m.batch_id = h.batch_id AND m.uuid = h.household_uuid
              AND m.responses ->> 'a02_relation_to_hh_head' = '1'
            GROUP BY m.batch_id, m.uuid
            HAVING count(*) = 1 AND max(concat_ws(' ',
                nullif(nullif(btrim(m.responses ->> 'a01_first_name'), ''), 'NA'),
                nullif(nullif(btrim(m.responses ->> 'a01_middle_name'), ''), 'NA'),
                nullif(nullif(btrim(m.responses ->> 'a01_last_name'), ''), 'NA')
            )) ILIKE %s ESCAPE '!'
        )"""
    return condition, f"%{pattern}%"


def _program_status(program, current_spisc=False):
    if program == "SPISC" and current_spisc:
        return "COALESCE(current_spisc.status, 'Needs Data Review')"
    return f"h.findings #>> '{{programs,{program},screening_status}}'"


def _priority_condition(priority, current_spisc=False):
    """Share the approved stored-result predicates between rows and selector counts."""
    score = "(h.findings #>> '{mpi,vulnerability_score}')::numeric"
    has_review = " OR ".join(
        f"{_program_status(code, current_spisc)} = 'For Review'"
        for code in PROGRAMS
    )
    if priority == "primary":
        score_rule, parameters = f"{score} > %s", (PRIMARY_CUTOFF,)
    elif priority == "secondary":
        score_rule = f"{score} >= %s AND {score} <= %s"
        parameters = (SECONDARY_MIN_SCORE, PRIMARY_CUTOFF)
    else:
        score_rule, parameters = f"{score} >= %s", (SECONDARY_MIN_SCORE,)
    rule = f"({score_rule} AND COALESCE(({has_review}), false))"
    return (f"NOT {rule}" if priority == "other" else rule), parameters


def _summary_selection(run_id, filters, *, spisc_age_statuses=None, batch_id=None):
    """Build shared count/page predicates from trusted SQL and bound values.

    Source filters use batch/UUID joins. No source or engine finding is changed,
    and identities remain bounded to the returned page.
    """
    filters = filters or AssessmentFilters()
    position = _reference_position(filters.search)
    joins = """
        FROM household_assessments h
        JOIN assessment_runs r ON r.id = h.run_id AND r.batch_id = h.batch_id
    """
    conditions = ["h.run_id = %s", "r.status = 'completed'"]
    parameters = [run_id]
    current_spisc = spisc_age_statuses is not None
    if current_spisc:
        # Python classifies distinct tokens once per operation. SQL only applies
        # the same household precedence. The LATERAL join looks up only this
        # household's members, and HAVING returns no row for an empty roster so
        # the LEFT JOIN leaves the status NULL, as before.
        joins += """
            LEFT JOIN LATERAL (
                SELECT CASE WHEN bool_or(ages.status = 'For Review') THEN 'For Review'
                            WHEN bool_or(ages.status = 'Needs Data Review') THEN 'Needs Data Review'
                            ELSE 'No Historical CBMS Trigger' END AS status
                FROM (
                    SELECT COALESCE(%s::jsonb ->> (m.responses ->> 'a07_age'),
                                    'Needs Data Review') AS status
                    FROM household_members m
                    WHERE m.batch_id = %s AND m.uuid = h.household_uuid
                ) ages
                HAVING count(*) > 0
            ) current_spisc ON true
        """
        parameters = [Jsonb(spisc_age_statuses), batch_id, *parameters]
    if position is not None or filters.barangay:
        joins += """
            JOIN households source
              ON source.batch_id = h.batch_id AND source.uuid = h.household_uuid
        """
    if filters.search:
        if position is not None:
            conditions.append("source.source_row_number = %s")
            parameters.append(position)
        else:
            condition, pattern = _head_search(filters.search)
            conditions.append(condition)
            parameters.append(pattern)
    if filters.barangay:
        conditions.append("btrim(source.responses ->> 'barangay') = %s")
        parameters.append(filters.barangay)
    if filters.programs:
        if filters.subsidy_mode == "exact":
            # Other statuses, including unknown findings, are not For Review.
            for program in PROGRAM_ORDER:
                operator = "=" if program in filters.programs else "IS DISTINCT FROM"
                if current_spisc and program == "SPISC":
                    conditions.append(f"{_program_status(program, True)} {operator} 'For Review'")
                else:
                    conditions.append(f"h.findings #>> %s {operator} 'For Review'")
                    parameters.append(["programs", program, "screening_status"])
        else:
            program_rules = []
            for program in filters.programs:
                if current_spisc and program == "SPISC":
                    program_rules.append(f"{_program_status(program, True)} = 'For Review'")
                else:
                    program_rules.append("h.findings #>> %s = 'For Review'")
                    parameters.append(["programs", program, "screening_status"])
            conditions.append("(" + " OR ".join(program_rules) + ")")
    if filters.completeness == "determined":
        conditions.append("(h.findings #>> '{mpi,unknown_indicator_count}')::integer = 0")
    elif filters.completeness == "unknown":
        conditions.append("(h.findings #>> '{mpi,unknown_indicator_count}')::integer > 0")
    if filters.priority != "all":
        rule, values = _priority_condition(filters.priority, current_spisc)
        conditions.append(rule)
        parameters.extend(values)
    return joins + " WHERE " + " AND ".join(conditions), tuple(parameters)


def count_assessment_summaries(
    connection, run_id, *, filters, spisc_age_statuses=None, batch_id=None,
):
    """Count all matching stored findings using the same predicates as the page."""
    selection, parameters = _summary_selection(
        run_id, filters, spisc_age_statuses=spisc_age_statuses, batch_id=batch_id,
    )
    return connection.execute("SELECT count(*) " + selection, parameters).fetchone()[0]


def count_priority_groups(
    connection, run_id, *, filters, spisc_age_statuses=None, batch_id=None,
):
    """Count groups in one SELECT, respecting every applied non-priority filter."""
    selection, parameters = _summary_selection(
        run_id, replace(filters, priority="all"),
        spisc_age_statuses=spisc_age_statuses, batch_id=batch_id,
    )
    dynamic = spisc_age_statuses is not None
    primary, primary_values = _priority_condition("primary", dynamic)
    secondary, secondary_values = _priority_condition("secondary", dynamic)
    total, first, next_group = connection.execute(
        f"SELECT count(*), count(*) FILTER (WHERE {primary}), "
        f"count(*) FILTER (WHERE {secondary}) " + selection,
        (*primary_values, *secondary_values, *parameters),
    ).fetchone()
    return {
        "all": total, "primary": first, "secondary": next_group,
        "both": first + next_group, "other": total - first - next_group,
    }


def count_dashboard_overview(
    connection, run_id, *, filters=None, spisc_age_statuses=None, batch_id=None,
):
    """Count priority groups, undetermined results and program findings in one pass.

    The stored findings are large, so each household's results are read once
    here instead of once per count. Priority rules and the SPISC current-age
    precedence match count_priority_groups and the Prioritization filters.
    """
    filters = filters or AssessmentFilters()
    selection, parameters = _summary_selection(
        run_id, filters, spisc_age_statuses=spisc_age_statuses, batch_id=batch_id,
    )
    spisc = (
        "COALESCE(current_status, 'Needs Data Review')" if spisc_age_statuses is not None
        else "stored -> 'SPISC' ->> 'screening_status'"
    )
    programs = (("4Ps", "four_ps"), ("SLP", "slp"), ("SPISC", "spisc"), ("TUPAD", "tupad"))
    program_counts = ", ".join(
        f"count(*) FILTER (WHERE {field} = 'For Review') AS {field}" for _, field in programs
    )
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            "SELECT count(*) AS assessed_households, "
            "count(*) FILTER (WHERE score > %s AND any_review) AS primary_group, "
            "count(*) FILTER (WHERE score >= %s AND score <= %s AND any_review) AS secondary_group, "
            "count(*) FILTER (WHERE unknown_count > 0) AS needs_attention, "
            + program_counts + " FROM ("
            "SELECT score, unknown_count, four_ps, slp, spisc, tupad, "
            "COALESCE(four_ps = 'For Review' OR slp = 'For Review' OR spisc = 'For Review' "
            "OR tupad = 'For Review', false) AS any_review FROM ("
            "SELECT (mpi ->> 'vulnerability_score')::numeric AS score, "
            "(mpi ->> 'unknown_indicator_count')::integer AS unknown_count, "
            "stored -> '4Ps' ->> 'screening_status' AS four_ps, "
            "stored -> 'SLP' ->> 'screening_status' AS slp, "
            f"{spisc} AS spisc, "
            "stored -> 'TUPAD' ->> 'screening_status' AS tupad FROM ("
            "SELECT h.findings -> 'mpi' AS mpi, h.findings -> 'programs' AS stored, "
            "current_spisc.status AS current_status " + selection
            + ") src) fields) counted",
            (PRIMARY_CUTOFF, SECONDARY_MIN_SCORE, PRIMARY_CUTOFF, *parameters),
        ).fetchone()
    first, next_group = row["primary_group"], row["secondary_group"]
    total = row["assessed_households"]
    return {
        "priority": {"all": total, "primary": first, "secondary": next_group,
                     "both": first + next_group, "other": total - first - next_group},
        "findings": {key: row[key] for key in (
            "assessed_households", "needs_attention", "four_ps", "slp", "spisc", "tupad")},
    }


STORED_INDICATOR_OUTCOMES = {"1": "deprived", "0": "not_deprived", "null": "unknown"}


def count_indicator_findings(connection, run_id, *, filters=None):
    """Count stored Deprived (1), Not Deprived (0) and Unknown (null) per indicator.

    Each indicator is expanded once per household, because reading every
    indicator separately re-reads the large stored document. An outcome is
    counted only when stored exactly as 1, 0 or null; a missing key or any
    other value is counted nowhere, so callers' reconciliation exposes it.
    Indicator names come from the approved weights, never from request input.
    """
    filters = filters or AssessmentFilters()
    selection, parameters = _summary_selection(run_id, filters)
    counts = {
        name: {outcome: 0 for outcome in STORED_INDICATOR_OUTCOMES.values()}
        for name in INDICATOR_WEIGHTS
    }
    for name, stored, households in connection.execute(
        "SELECT indicator.key, indicator.value::text, count(*) FROM ("
        "SELECT h.findings -> 'mpi' AS mpi " + selection + ") assessed "
        "CROSS JOIN LATERAL jsonb_each(assessed.mpi) indicator "
        "WHERE indicator.key = ANY(%s) GROUP BY 1, 2",
        (*parameters, list(INDICATOR_WEIGHTS)),
    ).fetchall():
        if stored in STORED_INDICATOR_OUTCOMES:
            counts[name][STORED_INDICATOR_OUTCOMES[stored]] = households
    return {
        "assessed_households": count_assessment_summaries(
            connection, run_id, filters=filters,
        ),
        "indicators": counts,
    }


def list_assessment_summaries(
    connection, run_id, *, limit, offset, filters=None, spisc_age_statuses=None, batch_id=None,
):
    """Filter in PostgreSQL before paging, projecting only unchanged summary fields."""
    selection, parameters = _summary_selection(
        run_id, filters, spisc_age_statuses=spisc_age_statuses, batch_id=batch_id,
    )
    spisc_status = _program_status("SPISC", spisc_age_statuses is not None)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT h.household_uuid, h.vulnerability_rank,
                      (h.findings #>> '{mpi,vulnerability_score}')::double precision
                          AS vulnerability_score,
                      (h.findings #>> '{mpi,coverage_pct}')::double precision AS coverage_pct,
                      h.findings #>> '{mpi,data_quality_status}' AS data_quality_status,
                      (h.findings #>> '{mpi,unknown_indicator_count}')::integer
                          AS unknown_indicator_count,
                      jsonb_build_object(
                          '4Ps', h.findings #>> '{programs,4Ps,screening_status}',
                          'SLP', h.findings #>> '{programs,SLP,screening_status}',
                          'SPISC', """ + spisc_status + """,
                          'TUPAD', h.findings #>> '{programs,TUPAD,screening_status}'
                      ) AS program_statuses
               """ + selection + """
               ORDER BY h.vulnerability_rank ASC, h.household_uuid ASC
               LIMIT %s OFFSET %s""", (*parameters, limit, offset),
        ).fetchall()
