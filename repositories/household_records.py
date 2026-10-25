"""Read page-sized household source summaries and focused source answers."""

from psycopg.rows import dict_row

from repositories.assessment_runs import _reference_position


HOUSEHOLD_FIELDS = (
    "q01_main_water", "q14_toilet_facility", "q19_share_toilet",
    "r03_roof", "r04_outer_walls", "r06_floor", "r07_floor_area",
    "r10_tenure", "r12_electricity",
)
MEMBER_FIELDS = (
    "a01_first_name", "a01_middle_name", "a01_last_name",
    "a02_relation_to_hh_head", "a05_sex", "a07_age",
    "c03_currently_attending_school", "e01_employed",
)


def _head_name_search(search):
    """Match the unique recorded head of the source household alias."""
    pattern = search.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    condition = """EXISTS (
            SELECT 1 FROM household_members m
            WHERE m.batch_id = h.batch_id AND m.uuid = h.uuid
              AND m.responses ->> 'a02_relation_to_hh_head' = '1'
            GROUP BY m.batch_id, m.uuid
            HAVING count(*) = 1 AND max(concat_ws(' ',
                nullif(nullif(btrim(m.responses ->> 'a01_first_name'), ''), 'NA'),
                nullif(nullif(btrim(m.responses ->> 'a01_middle_name'), ''), 'NA'),
                nullif(nullif(btrim(m.responses ->> 'a01_last_name'), ''), 'NA')
            )) ILIKE %s ESCAPE '!'
        )"""
    return condition, f"%{pattern}%"


def _selection(batch_id, run_id, search, barangay):
    """Use one selected-run, source-batch predicate for count and page queries."""
    conditions = ["h.batch_id = %s", "a.run_id = %s"]
    parameters = [batch_id, run_id]
    if search:
        position = _reference_position(search)
        if position is not None:
            conditions.append("h.source_row_number = %s")
            parameters.append(position)
        else:
            head_condition, pattern = _head_name_search(search)
            conditions.append(head_condition)
            parameters.append(pattern)
    if barangay:
        conditions.append("btrim(h.responses ->> 'barangay') = %s")
        parameters.append(barangay)
    return " AND ".join(conditions), tuple(parameters)


def count_records(connection, batch_id, run_id, *, search="", barangay=""):
    selection, parameters = _selection(batch_id, run_id, search, barangay)
    return connection.execute(
        """SELECT count(*) FROM households h
           JOIN household_assessments a
             ON a.batch_id = h.batch_id AND a.household_uuid = h.uuid
           WHERE """ + selection,
        parameters,
    ).fetchone()[0]


def list_records(connection, batch_id, run_id, *, search="", barangay="", limit, offset):
    """Order by the stable source reference and load identities for one page only."""
    selection, parameters = _selection(batch_id, run_id, search, barangay)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """WITH page AS (
                 SELECT h.uuid AS household_uuid, h.batch_id, h.source_row_number,
                        h.responses ->> 'barangay' AS barangay
                 FROM households h
                 JOIN household_assessments a
                   ON a.batch_id = h.batch_id AND a.household_uuid = h.uuid
                 WHERE """ + selection + """
                 ORDER BY h.source_row_number ASC LIMIT %s OFFSET %s
               )
               SELECT page.household_uuid, page.source_row_number, page.barangay,
                      roster.member_count, roster.head_count,
                      CASE WHEN roster.head_count = 1 THEN roster.first_name END AS first_name,
                      CASE WHEN roster.head_count = 1 THEN roster.middle_name END AS middle_name,
                      CASE WHEN roster.head_count = 1 THEN roster.last_name END AS last_name
               FROM page
               LEFT JOIN LATERAL (
                 SELECT count(*) AS member_count,
                        count(*) FILTER (WHERE m.responses ->> 'a02_relation_to_hh_head' = '1')
                            AS head_count,
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
                 WHERE m.batch_id = page.batch_id AND m.uuid = page.household_uuid
               ) roster ON true
               ORDER BY page.source_row_number ASC""",
            (*parameters, limit, offset),
        ).fetchall()


def find_record(connection, batch_id, run_id, household_uuid):
    """Read a single selected household's focused original answers."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT h.source_row_number, h.responses ->> 'barangay' AS barangay,
                      (SELECT jsonb_object_agg(field, h.responses -> field)
                       FROM unnest(%s::text[]) AS field) AS responses
               FROM households h
               JOIN household_assessments a
                 ON a.batch_id = h.batch_id AND a.household_uuid = h.uuid
               WHERE h.batch_id = %s AND h.uuid = %s AND a.run_id = %s""",
            (list(HOUSEHOLD_FIELDS), batch_id, household_uuid, run_id),
        ).fetchone()


def list_record_members(connection, batch_id, household_uuid):
    """Read only roster fields needed for the focused record."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT m.line_number,
                      (SELECT jsonb_object_agg(field, m.responses -> field)
                       FROM unnest(%s::text[]) AS field) AS responses
               FROM household_members m
               WHERE m.batch_id = %s AND m.uuid = %s
               ORDER BY m.line_number""",
            (list(MEMBER_FIELDS), batch_id, household_uuid),
        ).fetchall()
