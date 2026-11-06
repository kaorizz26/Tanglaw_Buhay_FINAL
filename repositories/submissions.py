"""Read page-sized lists of Citizen Accounts and document requests for the Submissions pages.

Everything is limited to one survey batch. These queries only read; they never change
accounts, requests or files, and they return no survey answers other than the barangay.
"""

from psycopg.rows import dict_row

from repositories.assessment_runs import _reference_position

BARANGAY_EXPRESSION = "btrim(h.responses ->> 'barangay')"
# Newest request first among finished ones; requests waiting for review come first,
# the one that has waited longest at the top.
REQUEST_ORDER = """(r.status <> 'submitted'),
                   CASE WHEN r.status = 'submitted' THEN r.updated_at END ASC,
                   r.updated_at DESC, r.id DESC"""


def _like_pattern(text):
    """Turn typed text into a 'contains' pattern that treats % and _ as plain characters."""
    escaped = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _filter_conditions(batch_id, search, status, barangay, status_column, name_columns):
    """Build the shared WHERE conditions; `name_columns` are the text columns to search.

    A search that looks like a household ID (HH-00012) matches that household. Any other
    text matches the given name columns.
    """
    conditions, parameters = ["a.batch_id = %s"], [batch_id]
    if search:
        position = _reference_position(search)
        if position is not None:
            conditions.append("h.source_row_number = %s")
            parameters.append(position)
        else:
            conditions.append("(" + " OR ".join(
                f"{column} ILIKE %s ESCAPE '!'" for column in name_columns) + ")")
            parameters.extend([_like_pattern(search)] * len(name_columns))
    if status:
        conditions.append(f"{status_column} = %s")
        parameters.append(status)
    if barangay:
        conditions.append(f"{BARANGAY_EXPRESSION} = %s")
        parameters.append(barangay)
    return " AND ".join(conditions), parameters


# ---- Citizen Accounts -----------------------------------------------------------

ACCOUNT_FROM = """FROM citizen_accounts a
                  JOIN households h ON h.batch_id = a.batch_id AND h.uuid = a.household_uuid"""


def _account_selection(batch_id, search, status, barangay):
    return _filter_conditions(
        batch_id, search, status, barangay, "a.status", ["a.representative_name"],
    )


def count_accounts(connection, batch_id, *, search="", status="", barangay=""):
    selection, parameters = _account_selection(batch_id, search, status, barangay)
    return connection.execute(
        f"SELECT count(*) {ACCOUNT_FROM} WHERE {selection}", parameters,
    ).fetchone()[0]


def list_accounts(connection, batch_id, *, search="", status="", barangay="", limit, offset):
    """Return one page of accounts, newest first, so a new account is easy to find."""
    selection, parameters = _account_selection(batch_id, search, status, barangay)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"""SELECT a.id, a.household_uuid, a.representative_name, a.status,
                       h.source_row_number, {BARANGAY_EXPRESSION} AS barangay
                {ACCOUNT_FROM} WHERE {selection}
                ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s""",
            parameters + [limit, offset],
        ).fetchall()


# ---- Document requests ----------------------------------------------------------

REQUEST_FROM = """FROM document_requests r
                  JOIN citizen_accounts a ON a.id = r.account_id
                  JOIN households h ON h.batch_id = a.batch_id AND h.uuid = a.household_uuid"""


def _request_selection(batch_id, search, status, barangay):
    return _filter_conditions(
        batch_id, search, status, barangay, "r.status", ["r.title", "a.representative_name"],
    )


def count_requests(connection, batch_id, *, search="", status="", barangay=""):
    selection, parameters = _request_selection(batch_id, search, status, barangay)
    return connection.execute(
        f"SELECT count(*) {REQUEST_FROM} WHERE {selection}", parameters,
    ).fetchone()[0]


def count_awaiting_review(connection, batch_id):
    """Count every request in the batch whose uploaded file is waiting for staff review."""
    return connection.execute(
        f"SELECT count(*) {REQUEST_FROM} WHERE a.batch_id = %s AND r.status = 'submitted'",
        (batch_id,),
    ).fetchone()[0]


def list_requests(connection, batch_id, *, search="", status="", barangay="", limit, offset):
    """Return one page of requests, those waiting for review first (longest wait on top)."""
    selection, parameters = _request_selection(batch_id, search, status, barangay)
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            f"""SELECT r.id, r.title, r.status, r.updated_at, a.representative_name,
                       h.source_row_number, {BARANGAY_EXPRESSION} AS barangay
                {REQUEST_FROM} WHERE {selection}
                ORDER BY {REQUEST_ORDER} LIMIT %s OFFSET %s""",
            parameters + [limit, offset],
        ).fetchall()
