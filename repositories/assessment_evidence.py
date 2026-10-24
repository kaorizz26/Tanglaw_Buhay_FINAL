"""Narrow source projections for one batch-scoped assessment; SELECTs only."""

from psycopg import sql
from psycopg.rows import dict_row

from services.mpi_indicator_content import (
    HEALTH_FIELDS, HOUSEHOLD_FIELDS, MEMBER_FIELDS, REFERENCE_FIELDS,
)
from services import subsidy_screening_content as screening


HOUSEHOLD_FIELDS = tuple(dict.fromkeys((*HOUSEHOLD_FIELDS, *screening.HOUSEHOLD_FIELDS)))
MEMBER_FIELDS = tuple(dict.fromkeys((*MEMBER_FIELDS, *screening.MEMBER_FIELDS)))
HEALTH_FIELDS = tuple(dict.fromkeys((*HEALTH_FIELDS, *screening.HEALTH_FIELDS)))
REFERENCE_FIELDS = tuple(dict.fromkeys((*REFERENCE_FIELDS, *screening.REFERENCE_FIELDS)))


def list_recorded_ages(connection, batch_id, household_uuid=None):
    """Read one roster's ages, or only distinct age tokens for a batch lookup.

    Text extraction preserves every age format accepted by the Python service.
    No names, complete rosters or Section L answers are fetched for batch counts.
    """
    selection = " AND uuid = %s" if household_uuid is not None else ""
    distinct = "" if household_uuid is not None else "DISTINCT "
    parameters = (batch_id, household_uuid) if household_uuid is not None else (batch_id,)
    return [row[0] for row in connection.execute(
        f"SELECT {distinct}responses ->> 'a07_age' FROM household_members "
        "WHERE batch_id = %s" + selection, parameters,
    ).fetchall()]


def find_household_sources(connection, batch_id, household_uuid):
    """Project assessment evidence, retaining missing values and exact source text."""
    row = connection.execute(
        """SELECT (
               SELECT jsonb_object_agg(field, h.responses -> field)
               FROM unnest(%s::text[]) AS field
           ) FROM households h WHERE h.batch_id = %s AND h.uuid = %s""",
        (list(HOUSEHOLD_FIELDS), batch_id, household_uuid),
    ).fetchone()
    return row[0] if row else None


def list_member_sources(connection, batch_id, household_uuid):
    """Join only this household's roster and corresponding Section L member lines."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT m.line_number,
                      (SELECT jsonb_object_agg(field, m.responses -> field)
                       FROM unnest(%s::text[]) AS field) AS responses,
                      l.section_l_line_number IS NOT NULL AS health_present,
                      (SELECT jsonb_object_agg(field, l.responses -> field)
                       FROM unnest(%s::text[]) AS field) AS health_responses
               FROM household_members m
               LEFT JOIN member_health l
                 ON l.batch_id = m.batch_id AND l.uuid = m.uuid
                 AND l.section_l_line_number = m.line_number
               WHERE m.batch_id = %s AND m.uuid = %s
               ORDER BY m.line_number""",
            (list(MEMBER_FIELDS), list(HEALTH_FIELDS), batch_id, household_uuid),
        ).fetchall()


def list_evidence_references(connection, batch_id):
    """Read the active batch's references for relevant variables, never fixtures."""
    references = {}
    for name, table in (
        ("codebook", "codebook_values"),
        ("mpi_mapping", "mpi_code_mappings"),
        ("definitions", "variable_definitions"),
    ):
        statement = sql.SQL(
            """SELECT responses FROM {} WHERE batch_id = %s
               AND responses ->> 'variable_name' = ANY(%s)
               ORDER BY source_row_number""",
        ).format(sql.Identifier(table))
        references[name] = [
            row[0] for row in connection.execute(
                statement, (batch_id, list(REFERENCE_FIELDS)),
            ).fetchall()
        ]
    return references
