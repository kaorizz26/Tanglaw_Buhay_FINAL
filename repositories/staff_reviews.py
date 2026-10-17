"""Independent staff-review storage; callers own transactions and authorization."""

from psycopg.rows import dict_row

from repositories.assessment_runs import _head_search, _reference_position


def _current_review_cte(*, household=False):
    """Shared selection: active work first, then latest completion, then cancellation.

    A withdrawn completion remains a barrier to older outcomes. Its effective
    values come from the existing append-only recommendation-state view.
    """
    scope = ' AND household_uuid = %s' if household else ''
    return """WITH scoped_reviews AS (
        SELECT * FROM staff_reviews WHERE batch_id = %s""" + scope + """
    ), current_reviews AS (
        SELECT DISTINCT ON (household_uuid, program)
            id AS current_review_id, household_uuid, program
        FROM scoped_reviews
        ORDER BY household_uuid, program,
            CASE status WHEN 'In Review' THEN 0 WHEN 'Closed' THEN 1 ELSE 2 END,
            created_at DESC, id DESC
    ), completed_reviews AS (
        SELECT DISTINCT ON (household_uuid, program)
            id AS completed_review_id, household_uuid, program
        FROM scoped_reviews WHERE status = 'Closed'
        ORDER BY household_uuid, program, created_at DESC, id DESC
    ) """


def _queue_selection(batch_id, run_id, filters, staff_id):
    statement = _current_review_cte()
    selection = """FROM scoped_reviews review
        JOIN current_reviews current ON current.current_review_id = review.id
        JOIN staff_review_recommendation_state state ON state.review_id = review.id
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
        conditions = ["review.status = 'Closed'", 'state.effective_recommendation = %s',
                      'NOT state.withdrawn']
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
            prefix + """SELECT review.*, state.effective_recommendation, h.vulnerability_rank,
                       responsible.username AS responsible_username """ + selection
            + ' ORDER BY ' + ordering + ' LIMIT %s OFFSET %s',
            (*parameters, limit, offset),
        ).fetchall()


def list_history(connection, batch_id, household_uuid):
    """Read household records, notes, assignments and recommendation changes in four queries."""
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            _current_review_cte(household=True) + """
               SELECT r.*, current.current_review_id, completed.completed_review_id,
                      state.effective_recommendation, state.effective_explanation,
                      state.effective_at, state.recommendation_version, state.withdrawn,
                      effective.username AS effective_username,
                      u.username, f.username AS finished_username,
                      c.username AS cancelled_username,
                      responsible.username AS responsible_username
               FROM scoped_reviews r JOIN staff_users u ON u.id = r.started_by
               JOIN current_reviews current USING (household_uuid, program)
               LEFT JOIN completed_reviews completed USING (household_uuid, program)
               JOIN staff_review_recommendation_state state ON state.review_id = r.id
               LEFT JOIN staff_users effective ON effective.id = state.effective_staff_id
               LEFT JOIN staff_users f ON f.id = r.finished_by
               LEFT JOIN staff_users c ON c.id = r.cancelled_by
               JOIN staff_users responsible ON responsible.id = r.responsible_staff_id
               ORDER BY r.created_at, r.id""", (batch_id, household_uuid),
        ).fetchall()
        if not rows:
            return rows
        indexed = {row['id']: row for row in rows}
        for row in rows:
            row['notes'] = []
            row['reassignments'] = []
            row['recommendation_changes'] = []
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
        changes = cursor.execute(
            """SELECT c.*, actor.username AS changed_username
               FROM staff_review_recommendation_changes c
               JOIN staff_reviews r ON r.id = c.review_id
               JOIN staff_users actor ON actor.id = c.changed_by
               WHERE r.batch_id = %s AND r.household_uuid = %s
               ORDER BY c.review_id, c.sequence""", (batch_id, household_uuid),
        ).fetchall()
        for change in changes:
            indexed[change['review_id']]['recommendation_changes'].append(change)
        return rows


def lock_household_reviews(connection, batch_id, household_uuid):
    """Serialize starts and completed changes before locking individual reviews."""
    return connection.execute(
        'SELECT uuid FROM households WHERE batch_id = %s AND uuid = %s FOR UPDATE',
        (batch_id, household_uuid),
    ).fetchone() is not None


def recommendation_state(connection, review_id):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            'SELECT * FROM staff_review_recommendation_state WHERE review_id = %s',
            (review_id,),
        ).fetchone()


def is_current_review(connection, review):
    current = connection.execute(
        _current_review_cte(household=True) +
        'SELECT current_review_id FROM current_reviews WHERE program = %s',
        (review['batch_id'], review['household_uuid'], review['program']),
    ).fetchone()
    return current is not None and current[0] == review['id']


def effective_completed_review(connection, batch_id, household_uuid, program):
    """Return only the latest completion; never skip a withdrawal for an older one."""
    return connection.execute(
        _current_review_cte(household=True) + """
        SELECT completed.completed_review_id FROM completed_reviews completed
        JOIN staff_review_recommendation_state state
          ON state.review_id = completed.completed_review_id
        WHERE completed.program = %s AND NOT state.withdrawn""",
        (batch_id, household_uuid, program),
    ).fetchone()


def append_recommendation_change(connection, review_id, state, action, staff_id,
                                 reason, recommendation, explanation):
    connection.execute(
        """INSERT INTO staff_review_recommendation_changes
           (review_id, sequence, action, previous_recommendation, previous_explanation,
            new_recommendation, new_explanation, reason, changed_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (review_id, state['recommendation_version'] + 1, action,
         state['effective_recommendation'], state['effective_explanation'],
         recommendation if action == 'correct' else None,
         explanation if action == 'correct' else None, reason, staff_id),
    )


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
