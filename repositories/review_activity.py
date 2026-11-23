"""Read retained staff-review actions and record administrator exports."""

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_ACTIVITY_CTE = """WITH review_actions AS (
    SELECT concat('started-', review.id) AS action_reference,
           review.id AS review_id, 'started'::text AS action,
           review.created_at AS action_at, review.started_by AS actor_id,
           NULL::bigint AS previous_staff_id, review.started_by AS responsible_staff_id,
           NULL::text AS recommendation
    FROM staff_reviews review
    UNION ALL
    SELECT concat('note-', note.id), note.review_id, 'note_added',
           note.created_at, note.authored_by, NULL, NULL, NULL
    FROM staff_review_notes note
    UNION ALL
    SELECT concat('reassigned-', assignment.id), assignment.review_id, 'reassigned',
           assignment.created_at, assignment.changed_by, assignment.previous_staff_id,
           assignment.responsible_staff_id, NULL
    FROM staff_review_reassignments assignment
    UNION ALL
    SELECT concat('finished-', review.id), review.id, 'finished',
           review.finished_at, review.finished_by, NULL, NULL,
           review.closing_recommendation
    FROM staff_reviews review WHERE review.finished_at IS NOT NULL
    UNION ALL
    SELECT concat('cancelled-', review.id), review.id, 'cancelled',
           review.cancelled_at, review.cancelled_by, NULL, NULL, NULL
    FROM staff_reviews review WHERE review.cancelled_at IS NOT NULL
    UNION ALL
    SELECT concat(change.action, '-', change.id), change.review_id,
           concat('recommendation_', change.action), change.created_at,
           change.changed_by, NULL, NULL,
           CASE WHEN change.action = 'correct' THEN change.new_recommendation
                ELSE change.previous_recommendation END
    FROM staff_review_recommendation_changes change
), activity AS (
    SELECT action.action_reference, action.review_id, action.action, action.action_at,
           review.batch_id, batch.survey_year, source.source_row_number,
           nullif(btrim(source.responses ->> 'barangay'), '') AS barangay,
           review.program, actor.username AS actor_username,
           previous.username AS previous_staff_username,
           responsible.username AS responsible_staff_username,
           action.recommendation
    FROM review_actions action
    JOIN staff_reviews review ON review.id = action.review_id
    JOIN dataset_batches batch ON batch.id = review.batch_id
    JOIN households source
      ON source.batch_id = review.batch_id AND source.uuid = review.household_uuid
    JOIN staff_users actor ON actor.id = action.actor_id
    LEFT JOIN staff_users previous ON previous.id = action.previous_staff_id
    LEFT JOIN staff_users responsible ON responsible.id = action.responsible_staff_id
) """

_ORDER_BY = {
    "newest": "activity.action_at DESC, activity.action_reference DESC",
    "oldest": "activity.action_at ASC, activity.action_reference ASC",
    "action": "activity.action ASC, activity.action_at ASC, activity.action_reference ASC",
    "action_desc": "activity.action DESC, activity.action_at DESC, activity.action_reference DESC",
    "staff": "lower(activity.actor_username) ASC, activity.action_at ASC",
    "staff_desc": "lower(activity.actor_username) DESC, activity.action_at DESC",
    "household": "activity.source_row_number ASC, activity.action_at ASC",
    "household_desc": "activity.source_row_number DESC, activity.action_at DESC",
    "program": "activity.program ASC, activity.action_at ASC",
    "program_desc": "activity.program DESC, activity.action_at DESC",
    "year": "activity.survey_year ASC, activity.batch_id ASC, activity.action_at ASC",
    "year_desc": "activity.survey_year DESC, activity.batch_id DESC, activity.action_at DESC",
    "batch": "activity.batch_id ASC, activity.action_at ASC",
    "batch_desc": "activity.batch_id DESC, activity.action_at DESC",
}


def _activity_filters(filters):
    """Build parameterized filters shared by count, page and export queries."""
    conditions = []
    parameters = []
    if filters.search:
        conditions.append(
            """(strpos(lower(activity.actor_username), lower(%s)) > 0
                 OR activity.source_row_number = %s)"""
        )
        parameters.extend((filters.search, filters.household_position))
    if filters.action:
        conditions.append("activity.action = %s")
        parameters.append(filters.action)
    if filters.program:
        conditions.append("activity.program = %s")
        parameters.append(filters.program)
    if filters.survey_year:
        conditions.append("activity.survey_year = %s")
        parameters.append(filters.survey_year)
    if filters.batch_id is not None:
        conditions.append("activity.batch_id = %s")
        parameters.append(filters.batch_id)
    if filters.started_at is not None:
        conditions.append("activity.action_at >= %s")
        parameters.append(filters.started_at)
    if filters.ended_before is not None:
        conditions.append("activity.action_at < %s")
        parameters.append(filters.ended_before)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where, tuple(parameters)


def count_actions(connection, filters):
    where, parameters = _activity_filters(filters)
    return connection.execute(
        _ACTIVITY_CTE + "SELECT count(*) FROM activity" + where,
        parameters,
    ).fetchone()[0]


def list_actions(connection, filters, *, limit=None, offset=0):
    """Return a filtered action page, or every matching action for export."""
    if filters.sort not in _ORDER_BY or type(offset) is not int or offset < 0:
        raise ValueError("invalid_review_activity_page")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError("invalid_review_activity_page")
    where, parameters = _activity_filters(filters)
    query = (
        _ACTIVITY_CTE + "SELECT * FROM activity" + where
        + " ORDER BY " + _ORDER_BY[filters.sort]
    )
    if limit is not None:
        query += " LIMIT %s OFFSET %s"
        parameters += (limit, offset)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, parameters).fetchall()


def list_filter_options(connection):
    """Return retained survey years and batch identifiers that contain reviews."""
    with connection.cursor(row_factory=dict_row) as cursor:
        years = cursor.execute(
            """SELECT DISTINCT batch.survey_year
               FROM staff_reviews review
               JOIN dataset_batches batch ON batch.id = review.batch_id
               ORDER BY batch.survey_year DESC""",
        ).fetchall()
        batches = cursor.execute(
            """SELECT DISTINCT review.batch_id, batch.survey_year
               FROM staff_reviews review
               JOIN dataset_batches batch ON batch.id = review.batch_id
               ORDER BY batch.survey_year DESC, review.batch_id DESC""",
        ).fetchall()
    return [row["survey_year"] for row in years], batches


def record_export(connection, administrator_id, filters, matching_actions):
    """Append one privacy-safe administrator export audit record."""
    connection.execute(
        """INSERT INTO staff_review_export_events
           (exported_by, filters, matching_actions)
           VALUES (%s, %s, %s)""",
        (administrator_id, Jsonb(filters), matching_actions),
    )
