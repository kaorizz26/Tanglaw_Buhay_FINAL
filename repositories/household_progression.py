"""Read historical designated runs without changing their batch selections."""

from psycopg.rows import dict_row

from repositories.assessment_runs import _reference_position


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


def list_unready_batches(connection):
    """Return validated imports that do not yet have a completed designated run."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT b.id AS batch_id, b.survey_year, b.created_at AS imported_at,
                      b.is_active
               FROM dataset_batches b
               LEFT JOIN assessment_run_selections s ON s.batch_id = b.id
               LEFT JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = b.id
               WHERE b.validation_status = 'validated'
                 AND (r.id IS NULL OR r.status <> 'completed' OR r.completed_at IS NULL)
               ORDER BY b.survey_year, b.id""",
        ).fetchall()


def list_ready_batch_household_counts(connection, batch_ids):
    """Count households in each ready batch's designated completed run."""
    if not batch_ids:
        return []
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT a.batch_id, count(*) AS households
               FROM household_assessments a
               JOIN assessment_run_selections s
                 ON s.batch_id = a.batch_id AND s.run_id = a.run_id
               WHERE a.batch_id = ANY(%s)
               GROUP BY a.batch_id""",
            (list(batch_ids),),
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


def list_comparison_findings(connection, earlier_batch_id, earlier_run_id,
                             later_batch_id, later_run_id):
    """Return the two stored findings side by side without deriving new results."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """WITH earlier AS (
                 SELECT household_uuid, findings
                 FROM household_assessments
                 WHERE batch_id = %s AND run_id = %s
               ), later AS (
                 SELECT household_uuid, findings
                 FROM household_assessments
                 WHERE batch_id = %s AND run_id = %s
               )
               SELECT coalesce(earlier.household_uuid, later.household_uuid)
                          AS household_uuid,
                      earlier.findings AS earlier_findings,
                      later.findings AS later_findings
               FROM earlier FULL JOIN later USING (household_uuid)
               ORDER BY household_uuid""",
            (earlier_batch_id, earlier_run_id, later_batch_id, later_run_id),
        ).fetchall()


def _search_scope():
    """Shared CTE for each household's most recent record in selected batches."""
    return """WITH history AS (
                 SELECT a.household_uuid, a.batch_id, b.survey_year
                 FROM household_assessments a
                 JOIN assessment_run_selections s
                   ON s.batch_id = a.batch_id AND s.run_id = a.run_id
                 JOIN assessment_runs r
                   ON r.id = a.run_id AND r.batch_id = a.batch_id
                 JOIN dataset_batches b ON b.id = a.batch_id
                 WHERE a.batch_id = ANY(%s)
                   AND b.validation_status = 'validated'
                   AND r.status = 'completed' AND r.completed_at IS NOT NULL
               ), latest AS (
                 SELECT DISTINCT ON (history.household_uuid)
                        history.household_uuid, history.batch_id, history.survey_year,
                        h.source_row_number, h.responses ->> 'barangay' AS barangay,
                        head.head_count, head.first_name, head.middle_name, head.last_name
                 FROM history
                 JOIN households h
                   ON h.batch_id = history.batch_id AND h.uuid = history.household_uuid
                 LEFT JOIN LATERAL (
                   SELECT count(*) FILTER (
                              WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                          ) AS head_count,
                          max(m.responses ->> 'a01_first_name') FILTER (
                              WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                          ) AS first_name,
                          max(m.responses ->> 'a01_middle_name') FILTER (
                              WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                          ) AS middle_name,
                          max(m.responses ->> 'a01_last_name') FILTER (
                              WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                          ) AS last_name
                   FROM household_members m
                   WHERE m.batch_id = history.batch_id
                     AND m.uuid = history.household_uuid
                 ) head ON true
                 ORDER BY history.household_uuid, history.survey_year DESC,
                          history.batch_id DESC
               ) """


def list_search_barangays(connection, batch_ids):
    """List barangays from each household's most recent selected-year record."""
    if not batch_ids:
        return []
    rows = connection.execute(
        _search_scope() +
        """SELECT DISTINCT btrim(barangay) AS barangay
           FROM latest
           WHERE btrim(coalesce(barangay, '')) NOT IN ('', 'NA')
           ORDER BY barangay""",
        (list(batch_ids),),
    ).fetchall()
    return [row[0] for row in rows]


def list_active_priority_households(connection, batch_id, run_id, limit=10):
    """Return the active run's highest-priority stored ranks without recalculation."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT a.household_uuid, b.survey_year, h.source_row_number,
                      h.responses ->> 'barangay' AS barangay,
                      head.head_count, head.first_name, head.middle_name, head.last_name,
                      (a.findings -> 'mpi' ->> 'vulnerability_rank')::integer
                          AS municipal_rank
               FROM household_assessments a
               JOIN dataset_batches b ON b.id = a.batch_id
               JOIN households h
                 ON h.batch_id = a.batch_id AND h.uuid = a.household_uuid
               LEFT JOIN LATERAL (
                 SELECT count(*) FILTER (
                            WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                        ) AS head_count,
                        max(m.responses ->> 'a01_first_name') FILTER (
                            WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                        ) AS first_name,
                        max(m.responses ->> 'a01_middle_name') FILTER (
                            WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                        ) AS middle_name,
                        max(m.responses ->> 'a01_last_name') FILTER (
                            WHERE m.responses ->> 'a02_relation_to_hh_head' = '1'
                        ) AS last_name
                 FROM household_members m
                 WHERE m.batch_id = a.batch_id AND m.uuid = a.household_uuid
               ) head ON true
               WHERE a.batch_id = %s AND a.run_id = %s AND b.is_active
                 AND b.validation_status = 'validated'
                 AND jsonb_typeof(a.findings -> 'mpi' -> 'vulnerability_rank') = 'number'
               ORDER BY (a.findings -> 'mpi' ->> 'vulnerability_rank')::integer,
                        a.household_uuid
               LIMIT %s""",
            (batch_id, run_id, limit),
        ).fetchall()


def search_progression_households(connection, batch_ids, query, search_by,
                                  barangay="", sort="reference", direction="asc", limit=25):
    """Search selected years using Household ID or the latest unique head name."""
    if not batch_ids:
        return []
    escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    if not query:
        search_condition = None
        search_value = None
    elif search_by == "reference":
        search_condition = "latest.source_row_number = %s"
        search_value = _reference_position(query)
        if search_value is None:
            raise ValueError("Unknown household reference.")
    elif search_by == "name":
        search_condition = """latest.head_count = 1 AND concat_ws(' ',
            nullif(nullif(btrim(latest.first_name), ''), 'NA'),
            nullif(nullif(btrim(latest.middle_name), ''), 'NA'),
            nullif(nullif(btrim(latest.last_name), ''), 'NA')
        ) ILIKE %s ESCAPE '!'"""
        search_value = "%" + escaped + "%"
    else:
        raise ValueError("Unknown progression search mode.")
    conditions = [search_condition] if search_condition else []
    parameters = [list(batch_ids)]
    if search_condition:
        parameters.append(search_value)
    if barangay:
        conditions.append("btrim(latest.barangay) = %s")
        parameters.append(barangay)
    parameters.append(limit)
    orders = {
        "reference": "latest.source_row_number",
        "head": "concat_ws(' ', latest.first_name, latest.middle_name, latest.last_name)",
        "barangay": "btrim(latest.barangay)",
    }
    if sort not in orders or direction not in {"asc", "desc"}:
        raise ValueError("Unknown progression ordering.")
    order = orders[sort] + (" DESC" if direction == "desc" else " ASC")
    statement = _search_scope() + """, years AS (
         SELECT household_uuid,
                array_agg(DISTINCT survey_year ORDER BY survey_year) AS years
         FROM history GROUP BY household_uuid
       )
       SELECT latest.household_uuid, latest.survey_year, latest.source_row_number,
              latest.barangay,
              latest.head_count, latest.first_name, latest.middle_name,
              latest.last_name, years.years
       FROM latest JOIN years USING (household_uuid)
       WHERE """ + " AND ".join(conditions) + """
       ORDER BY """ + order + ", latest.household_uuid ASC LIMIT %s"
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(statement, tuple(parameters)).fetchall()


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
