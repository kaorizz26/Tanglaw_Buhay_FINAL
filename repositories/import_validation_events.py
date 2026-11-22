"""Write aggregate validation audit metadata without source responses."""

from psycopg.types.json import Jsonb
from psycopg.rows import dict_row


_HISTORY_RECORD_NAMES = (
    "household",
    "section_a_e_indiv",
    "section_l02_to_l41",
    "section_l11_to_l14",
)


def _record_count_expression(name):
    """Return a trusted SQL expression for one aggregate file record count."""
    audit_path = f"'{{{name},records}}'"
    batch_path = f"'{{files,{name},records}}'"
    return f"""coalesce(
        CASE
            WHEN jsonb_typeof(event.file_metadata #> {audit_path}) = 'number'
            THEN (event.file_metadata #>> {audit_path})::bigint
        END,
        CASE
            WHEN jsonb_typeof(batch.source_provenance #> {batch_path}) = 'number'
            THEN (batch.source_provenance #>> {batch_path})::bigint
        END
    )"""


_HISTORY_RECORD_EXPRESSIONS = tuple(
    _record_count_expression(name) for name in _HISTORY_RECORD_NAMES
)
_HISTORY_TOTAL_RECORDS = (
    "CASE WHEN "
    + " AND ".join(f"({expression}) IS NOT NULL" for expression in _HISTORY_RECORD_EXPRESSIONS)
    + " THEN "
    + " + ".join(f"({expression})" for expression in _HISTORY_RECORD_EXPRESSIONS)
    + " END"
)
_HISTORY_RESULT_LABEL = """CASE event.outcome
    WHEN 'passed' THEN 'Validation passed'
    WHEN 'failed' THEN 'Validation failed'
    WHEN 'rejected' THEN 'Import rejected'
    WHEN 'staged' THEN 'Batch stored'
    WHEN 'deleted' THEN 'Batch deleted'
END"""
_HISTORY_ORDER_BY = {
    "newest": "coalesce(event.deleted_at, event.attempted_at) DESC, event.id DESC",
    "oldest": "coalesce(event.deleted_at, event.attempted_at) ASC, event.id ASC",
    "result": f"{_HISTORY_RESULT_LABEL} ASC, event.id ASC",
    "result_desc": f"{_HISTORY_RESULT_LABEL} DESC, event.id DESC",
    "year": "event.survey_year ASC NULLS LAST, event.id ASC",
    "year_desc": "event.survey_year DESC NULLS LAST, event.id ASC",
    "batch": (
        "coalesce(event.batch_id, event.deleted_batch_id) ASC NULLS LAST, event.id ASC"
    ),
    "batch_desc": (
        "coalesce(event.batch_id, event.deleted_batch_id) DESC NULLS LAST, event.id ASC"
    ),
    "admin": (
        "lower(coalesce(changer.username, uploader.username)) ASC, event.id ASC"
    ),
    "admin_desc": (
        "lower(coalesce(changer.username, uploader.username)) DESC, event.id DESC"
    ),
    "records": f"{_HISTORY_TOTAL_RECORDS} ASC NULLS LAST, event.id ASC",
    "records_desc": f"{_HISTORY_TOTAL_RECORDS} DESC NULLS LAST, event.id ASC",
}


def _history_filters(search, outcome, survey_year):
    """Build parameterized audit filters shared by the count and page queries."""
    conditions = []
    parameters = []
    if search:
        conditions.append(
            """(strpos(lower(uploader.username), lower(%s)) > 0
                 OR strpos(lower(coalesce(changer.username, uploader.username)),
                           lower(%s)) > 0
                 OR coalesce(event.batch_id, event.deleted_batch_id)::text = %s)"""
        )
        parameters.extend((search, search, search))
    if outcome:
        conditions.append("event.outcome = %s")
        parameters.append(outcome)
    if survey_year is not None:
        conditions.append("event.survey_year = %s")
        parameters.append(survey_year)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, tuple(parameters)


def record_attempt(connection, actor_id, outcome, reference_batch_id,
                   reference_version, file_metadata, error_count, warning_count,
                   batch_id=None, survey_year=None):
    """Record one administrator attempt after its safe result is known."""
    connection.execute(
        """INSERT INTO import_validation_events
           (actor_id, outcome, survey_year, batch_id, reference_batch_id, reference_version,
            file_metadata, error_count, warning_count)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (actor_id, outcome, survey_year, batch_id, reference_batch_id, reference_version,
         Jsonb(file_metadata), error_count, warning_count),
    )


def purge_expired_attempts(connection):
    """Apply the approved 90-day limit to standalone validation records."""
    return connection.execute(
        """DELETE FROM import_validation_events
           WHERE batch_id IS NULL
             AND outcome <> 'deleted'
             AND attempted_at < CURRENT_TIMESTAMP - INTERVAL '90 days'""",
    ).rowcount


def list_history(connection, *, limit=None):
    """Return retained aggregate import attempts without source record contents."""
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError("invalid_import_history_limit")
    query = """SELECT event.id, event.attempted_at, event.deleted_at, event.outcome,
                      event.survey_year,
                      coalesce(event.batch_id, event.deleted_batch_id) AS batch_id,
                      event.file_metadata, event.error_count, event.warning_count,
                      uploader.username AS uploaded_by,
                      coalesce(changer.username, uploader.username) AS action_by,
                      batch.source_provenance
               FROM import_validation_events event
               JOIN staff_users uploader ON uploader.id = event.actor_id
               LEFT JOIN staff_users changer ON changer.id = event.deleted_by
               LEFT JOIN dataset_batches batch ON batch.id = event.batch_id
               ORDER BY event.attempted_at DESC, event.id DESC"""
    parameters = ()
    if limit is not None:
        query += " LIMIT %s"
        parameters = (limit,)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, parameters).fetchall()


def count_history(connection, *, search="", outcome="", survey_year=None):
    """Count retained import events matching validated administrator filters."""
    where, parameters = _history_filters(search, outcome, survey_year)
    query = """SELECT count(*)
               FROM import_validation_events event
               JOIN staff_users uploader ON uploader.id = event.actor_id
               LEFT JOIN staff_users changer ON changer.id = event.deleted_by""" + where
    return connection.execute(query, parameters).fetchone()[0]


def list_history_years(connection):
    """Return retained survey years for the administrator filter control."""
    return [
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT survey_year
               FROM import_validation_events
               WHERE survey_year IS NOT NULL
               ORDER BY survey_year DESC""",
        ).fetchall()
    ]


def list_history_page(connection, *, search="", outcome="", survey_year=None,
                      sort="newest", limit=25, offset=0):
    """Return one filtered, consistently ordered page of aggregate audit events."""
    if (
        sort not in _HISTORY_ORDER_BY
        or type(limit) is not int or not 1 <= limit <= 1000
        or type(offset) is not int or offset < 0
    ):
        raise ValueError("invalid_import_history_page")
    where, parameters = _history_filters(search, outcome, survey_year)
    query = f"""SELECT event.id, event.attempted_at, event.deleted_at, event.outcome,
                       event.survey_year,
                       coalesce(event.batch_id, event.deleted_batch_id) AS batch_id,
                       event.file_metadata, event.error_count, event.warning_count,
                       uploader.username AS uploaded_by,
                       coalesce(changer.username, uploader.username) AS action_by,
                       batch.source_provenance
                FROM import_validation_events event
                JOIN staff_users uploader ON uploader.id = event.actor_id
                LEFT JOIN staff_users changer ON changer.id = event.deleted_by
                LEFT JOIN dataset_batches batch ON batch.id = event.batch_id
                {where}
                ORDER BY {_HISTORY_ORDER_BY[sort]}
                LIMIT %s OFFSET %s"""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, parameters + (limit, offset)).fetchall()
