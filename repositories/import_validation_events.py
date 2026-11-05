"""Write aggregate validation audit metadata without source responses."""

from psycopg.types.json import Jsonb


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
             AND attempted_at < CURRENT_TIMESTAMP - INTERVAL '90 days'""",
    ).rowcount
