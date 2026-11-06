"""Read historical designated runs without changing their batch selections."""

from psycopg.rows import dict_row


def list_ready_batches(connection):
    """Return validated batches with one explicitly designated completed run."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT b.id AS batch_id, b.survey_year, b.created_at AS imported_at,
                      b.is_active,
                      b.source_provenance, r.id AS run_id, r.calculation_provenance
               FROM dataset_batches b
               JOIN assessment_run_selections s ON s.batch_id = b.id
               JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = b.id
               WHERE b.validation_status = 'validated' AND r.status = 'completed'
                 AND r.completed_at IS NOT NULL
               ORDER BY b.survey_year, b.id""",
        ).fetchall()


def search_households(connection, prefix, limit=25):
    """Search UUIDs in all designated runs, including inactive historical batches."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT h.household_uuid, array_agg(DISTINCT b.survey_year
                                                ORDER BY b.survey_year) AS years
               FROM household_assessments h
               JOIN assessment_run_selections s
                 ON s.batch_id = h.batch_id AND s.run_id = h.run_id
               JOIN assessment_runs r ON r.id = h.run_id AND r.batch_id = h.batch_id
               JOIN dataset_batches b ON b.id = h.batch_id
               WHERE b.validation_status = 'validated' AND r.status = 'completed'
                 AND r.completed_at IS NOT NULL
                 AND h.household_uuid ILIKE %s
               GROUP BY h.household_uuid
               ORDER BY h.household_uuid LIMIT %s""",
            (prefix + "%", limit),
        ).fetchall()


def find_history(connection, household_uuid):
    """Read one household's findings only from designated completed runs."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT h.batch_id, h.run_id, h.findings
               FROM household_assessments h
               JOIN assessment_run_selections s
                 ON s.batch_id = h.batch_id AND s.run_id = h.run_id
               JOIN assessment_runs r ON r.id = h.run_id AND r.batch_id = h.batch_id
               JOIN dataset_batches b ON b.id = h.batch_id
               WHERE h.household_uuid = %s AND b.validation_status = 'validated'
                 AND r.status = 'completed' AND r.completed_at IS NOT NULL
               ORDER BY b.survey_year, b.id""",
            (household_uuid,),
        ).fetchall()


def find_run_provenance(connection, run_id, batch_id):
    """Resolve the original MPI run when a later run copied its MPI unchanged."""
    row = connection.execute(
        """SELECT calculation_provenance FROM assessment_runs
           WHERE id = %s AND batch_id = %s AND status = 'completed'""",
        (run_id, batch_id),
    ).fetchone()
    return row[0] if row else None


def find_mapping_fingerprint(connection, batch_id):
    """Compare the exact stored deprivation reference rows of two batches."""
    row = connection.execute(
        """SELECT md5(string_agg(responses::text, '' ORDER BY source_row_number))
           FROM mpi_code_mappings WHERE batch_id = %s""",
        (batch_id,),
    ).fetchone()
    return row[0] if row else None
