"""Anonymous Staff Portal reset requests and administrator request summaries."""

import re

from db import get_connection
from repositories import staff_password_resets


USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")


def request_staff_password_reset(username, *, connection_factory=get_connection):
    """Create or refresh a request only for an active LGU Staff username.

    Nothing is returned for any username, so routes can always show one neutral response.
    """
    normalized = username.strip().lower() if isinstance(username, str) else ""
    if not USERNAME_PATTERN.fullmatch(normalized):
        return
    with connection_factory() as connection:
        with connection.transaction():
            staff = staff_password_resets.find_requestable_staff(connection, normalized)
            if staff:
                staff_password_resets.create_or_refresh_pending(connection, staff["id"])


def password_reset_request_overview(*, connection_factory=get_connection):
    """Return pending requests and recent administrator completion history."""
    with connection_factory() as connection:
        return {
            "pending": staff_password_resets.list_pending(connection),
            "completed": staff_password_resets.list_recent_completed(connection),
        }


def pending_password_reset_count(*, connection_factory=get_connection):
    """Return the current pending request count for an administrator notification."""
    with connection_factory() as connection:
        return staff_password_resets.count_pending(connection)


def mark_reset_request_email_accepted(request_ids, *, connection_factory=get_connection):
    """Record SMTP acceptance without storing the temporary password or recipient."""
    ids = [item for item in request_ids if isinstance(item, int) and item > 0]
    if not ids:
        return
    with connection_factory() as connection:
        with connection.transaction():
            staff_password_resets.mark_email_accepted(connection, ids)
