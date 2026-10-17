"""Independent staff-review storage; callers own transactions and authorization."""

from psycopg.rows import dict_row

from repositories.assessment_runs import _head_search, _reference_position


def _queue_selection(batch_id, run_id, filters, staff_id):
    """Choose latest records before filtering; a cancellation supersedes history."""
    statement = """WITH latest AS (
        SELECT DISTINCT ON (household_uuid, program)
            id, household_uuid, program, status, closing_recommendation,
            created_at, finished_at, responsible_staff_id
        FROM staff_reviews WHERE batch_id = %s
        ORDER BY household_uuid, program, created_at DESC, id DESC
    ) """
    selection = """FROM latest review
        JOIN household_assessments h ON h.household_uuid = review.household_uuid
            AND h.batch_id = %s AND h.run_id = %s
        JOIN households source ON source.batch_id = h.batch_id
            AND source.uuid = h.household_uuid
        JOIN staff_users responsible ON responsible.id = review.responsible_staff_id
        WHERE """
    parameters = [batch_id, batch_id, run_id]
    if filters.status == 'in-review':
        conditions = ["review.status = 'In Review'"]
    else:
        conditions = ["review.status = 'Closed'", 'review.closing_recommendation = %s']
        parameters.append({
            'follow-up': 'For LGU Follow-Up', 'no-action': 'No Further Action',
        }[filters.status])
    if filters.scope == 'mine':
        conditions.append('review.responsible_staff_id = %s')
        parameters.append(staff_id)
    if filters.programs:
        conditions.append('review.program = ANY(%s)')
        parameters.append(list(filters.programs))
    if filters.barangay:
        conditions.append("btrim(source.responses ->> 'barangay') = %s")
        parameters.append(filters.barangay)
    if filters.search:
        position = _reference_position(filters.search)
        if position is not None:
            conditions.append('source.source_row_number = %s')
            parameters.append(position)
        else:
            condition, pattern = _head_search(filters.search)
            conditions.append(condition)
            parameters.append(pattern)
    return statement, selection + ' AND '.join(conditions), tuple(parameters)


def count_queue(connection, batch_id, run_id, filters, *, staff_id=None):
    prefix, selection, parameters = _queue_selection(batch_id, run_id, filters, staff_id)
    return connection.execute(prefix + 'SELECT count(*) ' + selection, parameters).fetchone()[0]


def list_queue(connection, batch_id, run_id, filters, *, limit, offset, staff_id=None):
    """Project bounded summary rows only; do not load evidence, findings or notes."""
    prefix, selection, parameters = _queue_selection(batch_id, run_id, filters, staff_id)
    column = 'review.created_at' if filters.status == 'in-review' else 'review.finished_at'
    direction = {'asc': 'ASC', 'desc': 'DESC'}[filters.date_order]
    ordering = f'{column} {direction}, review.id {direction}'
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            prefix + """SELECT review.*, h.vulnerability_rank,
                       responsible.username AS responsible_username """ + selection
            + ' ORDER BY ' + ordering + ' LIMIT %s OFFSET %s',
            (*parameters, limit, offset),
        ).fetchall()


def list_history(connection, batch_id, household_uuid):
    """Read household-scoped records, notes and assignment changes in three queries."""
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            """SELECT r.*, u.username, f.username AS finished_username,
                      c.username AS cancelled_username,
                      responsible.username AS responsible_username
               FROM staff_reviews r JOIN staff_users u ON u.id = r.started_by
               LEFT JOIN staff_users f ON f.id = r.finished_by
               LEFT JOIN staff_users c ON c.id = r.cancelled_by
               JOIN staff_users responsible ON responsible.id = r.responsible_staff_id
               WHERE r.batch_id = %s AND r.household_uuid = %s
               ORDER BY r.created_at, r.id""", (batch_id, household_uuid),
        ).fetchall()
        if not rows:
            return rows
        indexed = {row['id']: row for row in rows}
        for row in rows:
            row['notes'] = []
            row['reassignments'] = []
        notes = cursor.execute(
            """SELECT n.*, u.username FROM staff_review_notes n
               JOIN staff_reviews r ON r.id = n.review_id
               JOIN staff_users u ON u.id = n.authored_by
               WHERE r.batch_id = %s AND r.household_uuid = %s
               ORDER BY n.created_at, n.id""", (batch_id, household_uuid),
        ).fetchall()
        for note in notes:
            indexed[note['review_id']]['notes'].append(note)
        changes = cursor.execute(
            """SELECT a.*, previous.username AS previous_username,
                      responsible.username AS responsible_username, actor.username AS changed_username
               FROM staff_review_reassignments a
               JOIN staff_reviews r ON r.id = a.review_id
               JOIN staff_users previous ON previous.id = a.previous_staff_id
               JOIN staff_users responsible ON responsible.id = a.responsible_staff_id
               JOIN staff_users actor ON actor.id = a.changed_by
               WHERE r.batch_id = %s AND r.household_uuid = %s
               ORDER BY a.created_at, a.id""", (batch_id, household_uuid),
        ).fetchall()
        for change in changes:
            indexed[change['review_id']]['reassignments'].append(change)
        return rows


def list_assignable_staff(connection):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, username FROM staff_users WHERE is_active
               AND role IN ('system_administrator', 'lgu_staff') ORDER BY username, id""",
        ).fetchall()


def reassign(connection, review, staff_id, responsible_staff_id, reason):
    connection.execute(
        """INSERT INTO staff_review_reassignments
           (review_id, previous_staff_id, responsible_staff_id, changed_by, reason)
           VALUES (%s, %s, %s, %s, %s)""",
        (review['id'], review['responsible_staff_id'], responsible_staff_id, staff_id, reason),
    )
    connection.execute(
        """UPDATE staff_reviews SET responsible_staff_id = %s,
           responsibility_version = responsibility_version + 1 WHERE id = %s""",
        (responsible_staff_id, review['id']),
    )


def lock_review(connection, review_id, batch_id, household_uuid, program):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT * FROM staff_reviews WHERE id = %s AND batch_id = %s
               AND household_uuid = %s AND program = %s FOR UPDATE""",
            (review_id, batch_id, household_uuid, program),
        ).fetchone()


def add_note(connection, review_id, staff_id, body):
    connection.execute(
        """INSERT INTO staff_review_notes (review_id, authored_by, body)
           VALUES (%s, %s, %s)""", (review_id, staff_id, body),
    )


def finish(connection, review_id, staff_id, recommendation, explanation):
    connection.execute(
        """UPDATE staff_reviews SET status = 'Closed', closing_recommendation = %s,
           closing_explanation = %s, finished_by = %s, finished_at = clock_timestamp()
           WHERE id = %s AND status = 'In Review'""",
        (recommendation, explanation, staff_id, review_id),
    )


def cancel(connection, review_id, staff_id, reason):
    connection.execute(
        """UPDATE staff_reviews SET status = 'Cancelled', cancellation_reason = %s,
           cancelled_by = %s, cancelled_at = clock_timestamp()
           WHERE id = %s AND status = 'In Review'""", (reason, staff_id, review_id),
    )


def list_active(connection, batch_id, household_uuid):
    """Keep reviews tied to their original batch, including after run redesignation."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT r.id, r.program, r.status, r.starting_reason, r.created_at,
                      r.started_by, u.username
               FROM staff_reviews r JOIN staff_users u ON u.id = r.started_by
               WHERE r.batch_id = %s AND r.household_uuid = %s
                 AND r.status = 'In Review' ORDER BY r.id""",
            (batch_id, household_uuid),
        ).fetchall()


def lock_selected_context(connection, batch_id, run_id):
    """Hold selection and active state stable until the review insert commits."""
    return connection.execute(
        """SELECT b.id FROM dataset_batches b
           JOIN assessment_run_selections s ON s.batch_id = b.id
           JOIN assessment_runs r ON r.id = s.run_id AND r.batch_id = b.id
           WHERE b.id = %s AND r.id = %s AND b.is_active
             AND b.validation_status = 'validated' AND r.status = 'completed'
           FOR SHARE OF b, s, r""", (batch_id, run_id),
    ).fetchone() is not None


def authorized_staff_exists(connection, staff_id):
    """Recheck the initiating account while holding its role/activity stable."""
    return connection.execute(
        """SELECT id FROM staff_users WHERE id = %s AND is_active
           AND role IN ('system_administrator', 'lgu_staff') FOR SHARE""", (staff_id,),
    ).fetchone() is not None


def insert_active(connection, *, batch_id, household_uuid, run_id, program,
                  starting_reason, staff_id):
    """The partial unique index arbitrates even simultaneous initiation attempts."""
    row = connection.execute(
        """INSERT INTO staff_reviews
           (batch_id, household_uuid, assessment_run_id, program, starting_reason, started_by,
            responsible_staff_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (batch_id, household_uuid, program) WHERE status = 'In Review'
           DO NOTHING RETURNING id""",
        (batch_id, household_uuid, run_id, program, starting_reason, staff_id, staff_id),
    ).fetchone()
    return row[0] if row else None
