"""Independent staff-review storage; callers own transactions and authorization."""

from psycopg.rows import dict_row


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
           (batch_id, household_uuid, assessment_run_id, program, starting_reason, started_by)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (batch_id, household_uuid, program) WHERE status = 'In Review'
           DO NOTHING RETURNING id""",
        (batch_id, household_uuid, run_id, program, starting_reason, staff_id),
    ).fetchone()
    return row[0] if row else None
