"""Document request SQL; callers own connections and transactions.

Requests belong to a Citizen Account (and so to one household). Every lookup that
serves a citizen filters on the account in SQL, so a request or file of another
household simply is not found. Nothing here touches assessments or Staff Reviews.
"""

from psycopg.rows import dict_row

REQUEST_COLUMNS = """id, account_id, title, instructions, status, requested_by,
                     created_at, updated_at"""
FILE_COLUMNS = """id, request_id, version_number, display_name, content_type, size_bytes,
                  sha256, storage_key, uploaded_at"""


def _fetch_one(connection, statement, parameters=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(statement, parameters).fetchone()


def _fetch_all(connection, statement, parameters=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(statement, parameters).fetchall()


# ---- Requests -------------------------------------------------------------------

def insert_request(connection, account_id, staff_id, title, instructions):
    """Create a request for an Active account and return it, or None if not Active.

    The account row is held while the request is added, so the account cannot be
    deactivated between the check and the insert.
    """
    return _fetch_one(
        connection,
        f"""INSERT INTO document_requests (account_id, title, instructions, requested_by)
            SELECT a.id, %s, %s, %s FROM citizen_accounts a
            WHERE a.id = %s AND a.status = 'active' FOR SHARE OF a
            RETURNING {REQUEST_COLUMNS}""",
        (title, instructions, staff_id, account_id),
    )


def find_request(connection, request_id, *, lock=False):
    """Return one request for staff use; lock=True holds the row."""
    return _fetch_one(
        connection,
        f"SELECT {REQUEST_COLUMNS} FROM document_requests WHERE id = %s"
        + (" FOR UPDATE" if lock else ""),
        (request_id,),
    )


def find_request_for_account(connection, request_id, account_id, *, lock=False):
    """Return a request only if it belongs to this citizen's account."""
    return _fetch_one(
        connection,
        f"""SELECT {REQUEST_COLUMNS} FROM document_requests
            WHERE id = %s AND account_id = %s""" + (" FOR UPDATE" if lock else ""),
        (request_id, account_id),
    )


def list_requests_for_account(connection, account_id):
    """Return the account's requests, oldest first, with the latest upload's version and date."""
    return _fetch_all(
        connection,
        """SELECT r.id, r.title, r.instructions, r.status, r.created_at, r.updated_at,
                  f.version_number AS latest_version, f.uploaded_at AS latest_uploaded_at
           FROM document_requests r
           LEFT JOIN LATERAL (
               SELECT version_number, uploaded_at FROM document_files
               WHERE request_id = r.id ORDER BY version_number DESC LIMIT 1
           ) f ON true
           WHERE r.account_id = %s ORDER BY r.created_at, r.id""",
        (account_id,),
    )


def list_latest_files_for_account(connection, account_id):
    """Return the newest uploaded file of each of this citizen's requests."""
    return _fetch_all(
        connection,
        """SELECT DISTINCT ON (f.request_id) f.request_id, f.id, f.version_number,
                  f.display_name, f.size_bytes, f.uploaded_at
           FROM document_files f JOIN document_requests r ON r.id = f.request_id
           WHERE r.account_id = %s ORDER BY f.request_id, f.version_number DESC""",
        (account_id,),
    )


def list_latest_staff_messages_for_account(connection, account_id):
    """Return the newest correction or cancellation reason of each of this citizen's requests.

    Only the reason staff wrote for the citizen is returned; no staff name or id.
    """
    return _fetch_all(
        connection,
        """SELECT DISTINCT ON (e.request_id) e.request_id, e.action, e.note
           FROM document_request_events e JOIN document_requests r ON r.id = e.request_id
           WHERE r.account_id = %s AND e.action IN ('correction_requested', 'cancelled')
           ORDER BY e.request_id, e.created_at DESC, e.id DESC""",
        (account_id,),
    )


def change_status(connection, request_id, new_status):
    """Move a request to its next status and return it.

    The database refuses any step that is not allowed, for example from accepted.
    """
    return _fetch_one(
        connection,
        f"""UPDATE document_requests SET status = %s, updated_at = clock_timestamp()
            WHERE id = %s RETURNING {REQUEST_COLUMNS}""",
        (new_status, request_id),
    )


# ---- File versions --------------------------------------------------------------

def next_version_number(connection, request_id):
    """Return the version number the next upload of this request will get.

    Hold the request row (find_request_for_account with lock=True) first, so two
    uploads cannot choose the same number. The database also refuses a repeated
    number and a version above 10.
    """
    return connection.execute(
        "SELECT COALESCE(max(version_number), 0) + 1 FROM document_files WHERE request_id = %s",
        (request_id,),
    ).fetchone()[0]


def insert_file(connection, request_id, version_number, display_name, content_type,
                size_bytes, sha256, storage_key):
    """Record one uploaded file version; the file itself is already in private storage."""
    return _fetch_one(
        connection,
        f"""INSERT INTO document_files
                (request_id, version_number, display_name, content_type, size_bytes,
                 sha256, storage_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {FILE_COLUMNS}""",
        (request_id, version_number, display_name, content_type, size_bytes, sha256,
         storage_key),
    )


def list_files(connection, request_id):
    """Return every version of a request's file, newest first."""
    return _fetch_all(
        connection,
        f"SELECT {FILE_COLUMNS} FROM document_files WHERE request_id = %s "
        "ORDER BY version_number DESC",
        (request_id,),
    )


def find_file(connection, file_id):
    """Return a file record for staff use."""
    return _fetch_one(
        connection, f"SELECT {FILE_COLUMNS} FROM document_files WHERE id = %s", (file_id,),
    )


def find_file_for_account(connection, file_id, account_id):
    """Return a file record only if its request belongs to this citizen's account."""
    return _fetch_one(
        connection,
        """SELECT f.id, f.request_id, f.version_number, f.display_name, f.content_type,
                  f.size_bytes, f.sha256, f.storage_key, f.uploaded_at
           FROM document_files f JOIN document_requests r ON r.id = f.request_id
           WHERE f.id = %s AND r.account_id = %s""",
        (file_id, account_id),
    )


# ---- History --------------------------------------------------------------------

def insert_event(connection, request_id, action, *, staff_id=None, account_id=None,
                 file_id=None, note=None):
    """Append one action to the request's history (a staff member or the citizen acts)."""
    return _fetch_one(
        connection,
        """INSERT INTO document_request_events
               (request_id, action, staff_id, account_id, file_id, note)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id, request_id, action, staff_id, account_id, file_id, note, created_at""",
        (request_id, action, staff_id, account_id, file_id, note),
    )


def list_events(connection, request_id):
    """Return the request's history, oldest first, with the name of whoever acted."""
    return _fetch_all(
        connection,
        """SELECT e.id, e.action, e.note, e.file_id, e.created_at,
                  e.staff_id, COALESCE(s.display_name, s.username) AS staff_name,
                  e.account_id, a.representative_name AS citizen_name
           FROM document_request_events e
           LEFT JOIN staff_users s ON s.id = e.staff_id
           LEFT JOIN citizen_accounts a ON a.id = e.account_id
           WHERE e.request_id = %s ORDER BY e.created_at, e.id""",
        (request_id,),
    )
