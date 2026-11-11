"""SQL for Staff Portal password-reset requests; callers own transactions."""

from psycopg.rows import dict_row


def find_requestable_staff(connection, username):
    """Return only an active LGU Staff account matching the normalized username."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id FROM staff_users
               WHERE username = %s AND role = 'lgu_staff' AND is_active""",
            (username,),
        ).fetchone()


def create_or_refresh_pending(connection, staff_id):
    """Create one pending request or record another request for the same account."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """INSERT INTO staff_password_reset_requests (staff_id)
               VALUES (%s)
               ON CONFLICT (staff_id) WHERE status = 'pending'
               DO UPDATE SET request_count = staff_password_reset_requests.request_count + 1,
                             last_requested_at = clock_timestamp()
               RETURNING id""",
            (staff_id,),
        ).fetchone()


def count_pending(connection):
    """Return the number of staff accounts currently awaiting reset help."""
    return connection.execute(
        "SELECT count(*) FROM staff_password_reset_requests WHERE status = 'pending'",
    ).fetchone()[0]


def list_pending(connection):
    """Return pending requests with safe staff identity and account state."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT request.id, request.staff_id, request.request_count,
                      request.requested_at, request.last_requested_at,
                      staff.display_name, staff.username, staff.is_active
               FROM staff_password_reset_requests AS request
               JOIN staff_users AS staff ON staff.id = request.staff_id
               WHERE request.status = 'pending'
               ORDER BY request.last_requested_at DESC, request.id DESC""",
        ).fetchall()


def list_recent_completed(connection, limit=20):
    """Return recent completion metadata without recipient addresses or credentials."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT request.id, request.request_count, request.requested_at,
                      request.last_requested_at, request.completed_at,
                      request.delivery_method, request.email_accepted,
                      staff.display_name, staff.username,
                      administrator.display_name AS administrator_name,
                      administrator.username AS administrator_username
               FROM staff_password_reset_requests AS request
               JOIN staff_users AS staff ON staff.id = request.staff_id
               JOIN staff_users AS administrator ON administrator.id = request.completed_by
               WHERE request.status = 'completed'
               ORDER BY request.completed_at DESC, request.id DESC
               LIMIT %s""",
            (limit,),
        ).fetchall()


def complete_pending(connection, staff_id, administrator_id, delivery_method):
    """Complete every pending request covered by one successful credential action."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """UPDATE staff_password_reset_requests
               SET status = 'completed', completed_at = clock_timestamp(),
                   completed_by = %s, delivery_method = %s,
                   email_accepted = CASE WHEN %s = 'email' THEN false ELSE NULL END
               WHERE staff_id = %s AND status = 'pending'
               RETURNING id""",
            (administrator_id, delivery_method, delivery_method, staff_id),
        ).fetchall()


def mark_email_accepted(connection, request_ids):
    """Record SMTP acceptance after the credential transaction has committed."""
    connection.execute(
        """UPDATE staff_password_reset_requests SET email_accepted = true
           WHERE id = ANY(%s) AND status = 'completed'
             AND delivery_method = 'email' AND email_accepted = false""",
        (request_ids,),
    )
