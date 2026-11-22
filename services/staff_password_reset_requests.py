"""Anonymous Staff Portal reset requests and administrator request summaries."""

import re
from dataclasses import dataclass

from db import get_connection
from repositories import staff_password_resets


USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}")
RESET_REQUEST_STATE_OPTIONS = (
    ("pending", "Pending requests"),
    ("completed", "Completed requests"),
)
RESET_REQUEST_SORT_OPTIONS = (
    ("newest", "Newest activity"),
    ("oldest", "Oldest activity"),
    ("name", "Staff name A–Z"),
    ("name_desc", "Staff name Z–A"),
    ("attempts", "Fewest attempts"),
    ("attempts_desc", "Most attempts"),
    ("handled", "Handled by A–Z"),
    ("handled_desc", "Handled by Z–A"),
    ("delivery", "Delivery A–Z"),
    ("delivery_desc", "Delivery Z–A"),
)
RESET_REQUEST_FILTER_LENGTH = 200


class PasswordResetFilterError(ValueError):
    """Password reset request filters are malformed or unsupported."""


@dataclass(frozen=True)
class PasswordResetFilters:
    """Validated controls for the administrator's reset-request directory."""

    search: str = ""
    state: str = ""
    sort: str = "newest"

    @property
    def parameters(self):
        values = {"q": self.search, "state": self.state}
        if self.sort != "newest":
            values["sort"] = self.sort
        return {key: value for key, value in values.items() if value}

    @property
    def active(self):
        return bool(self.search or self.state or self.sort != "newest")


def parse_password_reset_filters(arguments):
    """Read one safe value for each reset-request list control."""
    values = {}
    for name, default in (("q", ""), ("state", ""), ("sort", "newest")):
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise PasswordResetFilterError("invalid_password_reset_filters")
        values[name] = supplied[0] if supplied else default
    search = " ".join(values["q"].split())
    valid_states = {value for value, _ in RESET_REQUEST_STATE_OPTIONS}
    valid_sorts = {value for value, _ in RESET_REQUEST_SORT_OPTIONS}
    if (
        len(search) > RESET_REQUEST_FILTER_LENGTH
        or "\x00" in search
        or values["state"] not in valid_states | {""}
        or values["sort"] not in valid_sorts
    ):
        raise PasswordResetFilterError("invalid_password_reset_filters")
    return PasswordResetFilters(search, values["state"], values["sort"])


def filter_password_reset_overview(overview, filters):
    """Filter recent reset activity without changing retained audit records."""
    if not isinstance(filters, PasswordResetFilters):
        raise PasswordResetFilterError("invalid_password_reset_filters")
    search = filters.search.casefold()

    def matches(item):
        return (
            not search
            or search in (item.get("display_name") or "").casefold()
            or search in item["username"].casefold()
        )

    pending = [item for item in overview["pending"] if matches(item)]
    completed = [item for item in overview["completed"] if matches(item)]
    if filters.state == "pending":
        completed = []
    elif filters.state == "completed":
        pending = []

    if filters.sort in {"name", "name_desc"}:
        def key(item):
            return ((item.get("display_name") or item["username"]).casefold(), item["id"])

        reverse = filters.sort == "name_desc"
        pending.sort(key=key, reverse=reverse)
        completed.sort(key=key, reverse=reverse)
    elif filters.sort in {"attempts", "attempts_desc"}:
        pending.sort(
            key=lambda item: (item["request_count"], item["id"]),
            reverse=filters.sort == "attempts_desc",
        )
    elif filters.sort in {"handled", "handled_desc"}:
        completed.sort(
            key=lambda item: (
                (item.get("administrator_name") or item.get("administrator_username") or "").casefold(),
                item["id"],
            ),
            reverse=filters.sort == "handled_desc",
        )
    elif filters.sort in {"delivery", "delivery_desc"}:
        completed.sort(
            key=lambda item: (item.get("delivery_method") or "", item["id"]),
            reverse=filters.sort == "delivery_desc",
        )
    else:
        reverse = filters.sort == "newest"
        pending.sort(key=lambda item: item["last_requested_at"], reverse=reverse)
        completed.sort(key=lambda item: item["completed_at"], reverse=reverse)
    return {
        "pending": pending,
        "completed": completed,
        "totals": {
            "pending": len(overview["pending"]),
            "completed": len(overview["completed"]),
        },
    }


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
