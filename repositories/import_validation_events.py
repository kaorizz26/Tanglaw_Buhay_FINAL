"""Write aggregate validation audit metadata without source responses."""

from psycopg.types.json import Jsonb
from psycopg.rows import dict_row


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
