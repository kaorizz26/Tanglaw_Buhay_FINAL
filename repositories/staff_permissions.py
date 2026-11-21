"""SQL for LGU Staff access grants and their administrator audit history."""

from psycopg.rows import dict_row


def list_catalog(connection):
    """Return the ordered permission definitions used by the Access Manager."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT code, label, description, sort_order
               FROM staff_permissions ORDER BY sort_order"""
        ).fetchall()


def find_staff_target(connection, staff_id, *, lock=False):
    """Return one LGU Staff account, including inactive accounts."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, display_name, username, is_active, session_version
               FROM staff_users
               WHERE id = %s AND role = 'lgu_staff'"""
            + (" FOR UPDATE" if lock else ""),
            (staff_id,),
        ).fetchone()


def find_active_administrator(connection, administrator_id):
    """Lock and verify the administrator responsible for an access change."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id FROM staff_users
               WHERE id = %s AND role = 'system_administrator' AND is_active
               FOR SHARE""",
            (administrator_id,),
        ).fetchone()


def list_codes(connection, staff_id, *, lock=False):
    """Return one staff account's permission codes, optionally locking the grants."""
    query = (
        "SELECT permission_code FROM staff_user_permissions "
        "WHERE staff_id = %s ORDER BY permission_code"
    )
    if lock:
        query += " FOR UPDATE"
    return [row[0] for row in connection.execute(query, (staff_id,)).fetchall()]


def add_grants(connection, staff_id, permission_codes, administrator_id):
    """Insert administrator-issued grants in deterministic order."""
    for code in sorted(permission_codes):
        connection.execute(
            """INSERT INTO staff_user_permissions
               (staff_id, permission_code, granted_by, grant_source)
               VALUES (%s, %s, %s, 'administrator')""",
            (staff_id, code, administrator_id),
        )


def remove_grants(connection, staff_id, permission_codes):
    """Remove the selected grants; dependency checks run when the transaction commits."""
    for code in sorted(permission_codes):
        connection.execute(
            """DELETE FROM staff_user_permissions
               WHERE staff_id = %s AND permission_code = %s""",
            (staff_id, code),
        )


def record_events(connection, staff_id, administrator_id, permission_codes, action):
    """Append one audit event for every permission that changed."""
    for code in sorted(permission_codes):
        connection.execute(
            """INSERT INTO staff_permission_events
               (staff_id, permission_code, action, changed_by)
               VALUES (%s, %s, %s, %s)""",
            (staff_id, code, action, administrator_id),
        )


def list_events(connection, staff_id, *, limit=50):
    """Return recent access changes with the responsible administrator."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT events.id, events.permission_code, permissions.label,
                      events.action, events.changed_at,
                      actors.id AS administrator_id,
                      COALESCE(actors.display_name, actors.username) AS administrator_name
               FROM staff_permission_events AS events
               JOIN staff_permissions AS permissions
                 ON permissions.code = events.permission_code
               JOIN staff_users AS actors ON actors.id = events.changed_by
               WHERE events.staff_id = %s
               ORDER BY events.changed_at DESC, events.id DESC
               LIMIT %s""",
            (staff_id, limit),
        ).fetchall()
