"""Citizen account SQL; callers own connections and transactions.

This module never reads survey answers. It only stores the link to a household
record, the representative's details and the citizen's credentials.
"""

from psycopg.rows import dict_row

# Account fields that are safe to pass around; the password hash is selected separately.
ACCOUNT_COLUMNS = """id, batch_id, household_uuid, representative_name, email, status,
                     session_version, failed_login_count, locked_until, last_login_at,
                     activated_at, created_at"""


def _fetch_one(connection, statement, parameters=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(statement, parameters).fetchone()


def insert_account(connection, batch_id, household_uuid, representative_name, email):
    """Create a pending account for one household record and return it."""
    return _fetch_one(
        connection,
        f"""INSERT INTO citizen_accounts
                (batch_id, household_uuid, representative_name, email)
            VALUES (%s, %s, %s, %s) RETURNING {ACCOUNT_COLUMNS}""",
        (batch_id, household_uuid, representative_name, email),
    )


def find_by_id(connection, account_id, *, lock=False):
    """Return one account without its password hash; lock=True holds the row."""
    return _fetch_one(
        connection,
        f"SELECT {ACCOUNT_COLUMNS} FROM citizen_accounts WHERE id = %s"
        + (" FOR UPDATE" if lock else ""),
        (account_id,),
    )


def find_by_household(connection, batch_id, household_uuid):
    """Return the household's account without its password hash, or None."""
    return _fetch_one(
        connection,
        f"""SELECT {ACCOUNT_COLUMNS} FROM citizen_accounts
            WHERE batch_id = %s AND household_uuid = %s""",
        (batch_id, household_uuid),
    )


def find_by_email(connection, email):
    """Return the login record, including its password hash, or None."""
    return _fetch_one(
        connection,
        f"SELECT {ACCOUNT_COLUMNS}, password_hash FROM citizen_accounts WHERE email = %s",
        (email,),
    )


def activate(connection, account_id, password_hash):
    """Store the citizen's chosen password on a pending account and make it active."""
    return _fetch_one(
        connection,
        f"""UPDATE citizen_accounts
            SET password_hash = %s, status = 'active', activated_at = CURRENT_TIMESTAMP,
                failed_login_count = 0, locked_until = NULL,
                session_version = session_version + 1
            WHERE id = %s AND status = 'pending_activation'
            RETURNING {ACCOUNT_COLUMNS}""",
        (password_hash, account_id),
    )


def replace_password(connection, account_id, password_hash):
    """Change an active account's password and end all of its sessions."""
    return _fetch_one(
        connection,
        f"""UPDATE citizen_accounts
            SET password_hash = %s, failed_login_count = 0, locked_until = NULL,
                session_version = session_version + 1
            WHERE id = %s AND status = 'active'
            RETURNING {ACCOUNT_COLUMNS}""",
        (password_hash, account_id),
    )


def return_to_pending(connection, account_id, email=None):
    """Clear the password and require activation again, optionally with a new email.

    Used for reactivation and email changes. The caller decides whether the
    change is allowed; this only applies it and ends all sessions.
    """
    return _fetch_one(
        connection,
        f"""UPDATE citizen_accounts
            SET status = 'pending_activation', password_hash = NULL,
                email = COALESCE(%s, email), failed_login_count = 0, locked_until = NULL,
                session_version = session_version + 1
            WHERE id = %s
            RETURNING {ACCOUNT_COLUMNS}""",
        (email, account_id),
    )


def deactivate(connection, account_id):
    """Block the account and end all of its sessions."""
    return _fetch_one(
        connection,
        f"""UPDATE citizen_accounts
            SET status = 'deactivated', session_version = session_version + 1
            WHERE id = %s RETURNING {ACCOUNT_COLUMNS}""",
        (account_id,),
    )


def record_failed_login(connection, account_id, max_failures, lock_minutes):
    """Count a failed sign-in and lock the account when the limit is reached."""
    return _fetch_one(
        connection,
        """UPDATE citizen_accounts
           SET failed_login_count = failed_login_count + 1,
               locked_until = CASE WHEN failed_login_count + 1 >= %s
                   THEN CURRENT_TIMESTAMP + make_interval(mins => %s)
                   ELSE locked_until END
           WHERE id = %s
           RETURNING failed_login_count, locked_until""",
        (max_failures, lock_minutes, account_id),
    )


def record_successful_login(connection, account_id):
    """Reset the failure count and remember when the citizen last signed in."""
    connection.execute(
        """UPDATE citizen_accounts
           SET failed_login_count = 0, locked_until = NULL,
               last_login_at = CURRENT_TIMESTAMP
           WHERE id = %s""",
        (account_id,),
    )


def insert_event(connection, account_id, staff_id, action, note, *,
                 representative_verified=False, privacy_consent_confirmed=False):
    """Append one staff action to the account's history."""
    return _fetch_one(
        connection,
        """INSERT INTO citizen_account_events
               (account_id, staff_id, action, note, representative_verified,
                privacy_consent_confirmed)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id, account_id, staff_id, action, note, representative_verified,
                     privacy_consent_confirmed, created_at""",
        (account_id, staff_id, action, note, representative_verified,
         privacy_consent_confirmed),
    )


def list_events(connection, account_id):
    """Return the account's history oldest first, with each staff member's name."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            """SELECT e.id, e.action, e.note, e.representative_verified,
                      e.privacy_consent_confirmed, e.created_at, e.staff_id,
                      s.username AS staff_username, s.display_name AS staff_display_name
               FROM citizen_account_events e
               JOIN staff_users s ON s.id = e.staff_id
               WHERE e.account_id = %s ORDER BY e.created_at, e.id""",
            (account_id,),
        ).fetchall()


def revoke_open_tokens(connection, account_id, purpose=None):
    """Cancel the account's unused links (all, or one purpose); return how many."""
    return connection.execute(
        """UPDATE citizen_account_tokens SET revoked_at = CURRENT_TIMESTAMP
           WHERE account_id = %s AND used_at IS NULL AND revoked_at IS NULL
             AND (%s::text IS NULL OR purpose = %s::text)""",
        (account_id, purpose, purpose),
    ).rowcount


def insert_token(connection, account_id, purpose, token_hash, code_hash, lifetime):
    """Store a new link (and code) hash; the caller must revoke the old pair first."""
    return _fetch_one(
        connection,
        """INSERT INTO citizen_account_tokens
               (account_id, purpose, token_hash, code_hash, expires_at)
           VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP + %s)
           RETURNING id, account_id, purpose, expires_at, created_at""",
        (account_id, purpose, token_hash, code_hash, lifetime),
    )


def find_open_token(connection, token_hash, purpose, *, lock=False):
    """Return a usable link's record with its account's status, or None.

    Usable means not used, not revoked and not expired. lock=True holds the
    token row so two requests cannot use or count against it at the same time.
    """
    return _fetch_one(
        connection,
        """SELECT t.id, t.account_id, t.purpose, t.code_hash, t.failed_code_attempts,
                  t.expires_at, a.status AS account_status
           FROM citizen_account_tokens t
           JOIN citizen_accounts a ON a.id = t.account_id
           WHERE t.token_hash = %s AND t.purpose = %s
             AND t.used_at IS NULL AND t.revoked_at IS NULL
             AND t.expires_at > CURRENT_TIMESTAMP"""
        + (" FOR UPDATE OF t" if lock else ""),
        (token_hash, purpose),
    )


def record_failed_code_attempt(connection, token_id, max_attempts):
    """Count a wrong code; the last allowed wrong code cancels the link as well.

    Returns the new attempt count and whether the link is now cancelled. Commit
    this change even though the sign-in step failed, or the limit would not hold.
    """
    return _fetch_one(
        connection,
        """UPDATE citizen_account_tokens
           SET failed_code_attempts = failed_code_attempts + 1,
               revoked_at = CASE WHEN failed_code_attempts + 1 >= %s
                   THEN CURRENT_TIMESTAMP ELSE revoked_at END
           WHERE id = %s AND used_at IS NULL AND revoked_at IS NULL
           RETURNING failed_code_attempts, revoked_at IS NOT NULL AS revoked""",
        (max_attempts, token_id),
    )


def consume_token(connection, token_id):
    """Mark a usable link as used; return False if it was already used or cancelled."""
    return connection.execute(
        """UPDATE citizen_account_tokens SET used_at = CURRENT_TIMESTAMP
           WHERE id = %s AND used_at IS NULL AND revoked_at IS NULL
             AND expires_at > CURRENT_TIMESTAMP""",
        (token_id,),
    ).rowcount == 1


def mark_email_accepted(connection, token_id):
    """Record that the mail server accepted the message (not that it was delivered)."""
    connection.execute(
        "UPDATE citizen_account_tokens SET email_accepted_at = CURRENT_TIMESTAMP WHERE id = %s",
        (token_id,),
    )


def active_lgu_staff_exists(connection, staff_id):
    """Recheck, while holding the row, that the acting account is an active LGU Staff member."""
    return connection.execute(
        """SELECT id FROM staff_users
           WHERE id = %s AND is_active AND role = 'lgu_staff' FOR SHARE""",
        (staff_id,),
    ).fetchone() is not None


def find_panel(connection, batch_id, household_uuid):
    """Return what the Assessment page shows about a household's account, or None.

    It joins the newest activation link so staff can see whether the email was
    accepted by the mail server and whether the link and code are still usable.
    """
    return _fetch_one(
        connection,
        """SELECT a.id, a.representative_name, a.email, a.status, a.created_at,
                  a.activated_at, t.email_accepted_at, t.expires_at,
                  COALESCE(t.used_at IS NULL AND t.revoked_at IS NULL
                           AND t.expires_at > CURRENT_TIMESTAMP, false) AS link_valid
           FROM citizen_accounts a
           LEFT JOIN LATERAL (
               SELECT * FROM citizen_account_tokens
               WHERE account_id = a.id AND purpose = 'activation'
               ORDER BY created_at DESC, id DESC LIMIT 1
           ) t ON true
           WHERE a.batch_id = %s AND a.household_uuid = %s""",
        (batch_id, household_uuid),
    )
