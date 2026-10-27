"""Staff account SQL; callers own connections and transactions."""

from psycopg.rows import dict_row


def find_by_username(connection, username):
    """Return the login record, including its hash, or None."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT * FROM staff_users WHERE username = %s", (username,),
        ).fetchone()


def find_by_id(connection, user_id):
    """Return authorization state without retrieving the password hash."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, username, display_name, role, is_active, session_version,
                      must_change_password
               FROM staff_users WHERE id = %s""", (user_id,),
        ).fetchone()


def find_for_password_change(connection, user_id):
    """Lock one account while verifying and replacing its initial password."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT id, password_hash, role, is_active, session_version,
                      must_change_password
               FROM staff_users WHERE id = %s FOR UPDATE""", (user_id,),
        ).fetchone()


def complete_initial_password_change(connection, user_id, password_hash):
    """Store the new hash and revoke older sessions atomically."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """UPDATE staff_users
               SET password_hash = %s, must_change_password = false,
                   session_version = session_version + 1
               WHERE id = %s
               RETURNING id, role, session_version""",
            (password_hash, user_id),
        ).fetchone()


def invalidate_sessions(connection, user_id):
    """Revoke all previously issued sessions for this account."""
    connection.execute(
        "UPDATE staff_users SET session_version = session_version + 1 WHERE id = %s",
        (user_id,),
    )


def insert_initial_admin(connection, username, password_hash):
    """Bootstrap once, serializing concurrent attempts without replacing accounts."""
    connection.execute("LOCK TABLE staff_users IN EXCLUSIVE MODE")
    if connection.execute("SELECT EXISTS (SELECT 1 FROM staff_users)").fetchone()[0]:
        raise ValueError("Initial setup requires an empty staff account table.")
    connection.execute(
        """INSERT INTO staff_users (username, password_hash, role)
           VALUES (%s, %s, 'system_administrator')""", (username, password_hash),
    )


def insert_staff(connection, display_name, username, password_hash):
    """Create an active LGU Staff account with a required password change."""
    connection.execute(
        """INSERT INTO staff_users
           (display_name, username, password_hash, role, is_active,
            must_change_password)
           VALUES (%s, %s, %s, 'lgu_staff', true, true)""",
        (display_name, username, password_hash),
    )
