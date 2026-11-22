"""Validation and credential creation for administrator-created staff accounts."""

import re
from dataclasses import dataclass
from datetime import timedelta, timezone
from secrets import token_urlsafe

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_password_resets, staff_users
from services.email_delivery import (
    EmailDeliveryError, build_staff_login_link, check_recipient, load_email_settings,
)


# Twelve random bytes encode to exactly 16 URL-safe characters without padding.
TEMPORARY_PASSWORD_BYTES = 12
STAFF_ACCOUNT_STATUS_OPTIONS = (
    ("ready", "Ready"),
    ("pending", "Pending password setup"),
    ("inactive", "Inactive"),
)
STAFF_ACCOUNT_SORT_OPTIONS = (
    ("newest", "Newest created"),
    ("oldest", "Oldest created"),
    ("name", "Staff name A–Z"),
    ("name_desc", "Staff name Z–A"),
    ("username", "Username A–Z"),
    ("username_desc", "Username Z–A"),
    ("status", "Status A–Z"),
    ("status_desc", "Status Z–A"),
)
STAFF_ACCOUNT_FILTER_LENGTH = 200


class StaffAccountInputError(ValueError):
    """A staff account request cannot be completed in its current state."""


class StaffAccountHasActiveReviews(StaffAccountInputError):
    """The staff member must be relieved of active reviews first."""


@dataclass(frozen=True)
class StaffAccountFilters:
    """Validated controls for the administrator's staff account directory."""

    search: str = ""
    status: str = ""
    sort: str = "newest"

    @property
    def parameters(self):
        values = {"q": self.search, "status": self.status}
        if self.sort != "newest":
            values["sort"] = self.sort
        return {key: value for key, value in values.items() if value}

    @property
    def active(self):
        return bool(self.search or self.status or self.sort != "newest")


def parse_staff_account_filters(arguments):
    """Read one safe value for each staff-directory filter from a query string."""
    values = {}
    for name, default in (("q", ""), ("status", ""), ("sort", "newest")):
        supplied = arguments.getlist(name)
        if len(supplied) > 1:
            raise StaffAccountInputError("Choose valid staff account filters.")
        values[name] = supplied[0] if supplied else default
    search = " ".join(values["q"].split())
    valid_statuses = {value for value, _ in STAFF_ACCOUNT_STATUS_OPTIONS}
    valid_sorts = {value for value, _ in STAFF_ACCOUNT_SORT_OPTIONS}
    if (
        len(search) > STAFF_ACCOUNT_FILTER_LENGTH
        or "\x00" in search
        or values["status"] not in valid_statuses | {""}
        or values["sort"] not in valid_sorts
    ):
        raise StaffAccountInputError("Choose valid staff account filters.")
    return StaffAccountFilters(search, values["status"], values["sort"])


def _staff_account_status(account):
    if not account["is_active"]:
        return "inactive"
    return "pending" if account["must_change_password"] else "ready"


def filter_staff_accounts(accounts, filters):
    """Filter and order already-authorized staff directory rows."""
    if not isinstance(filters, StaffAccountFilters):
        raise StaffAccountInputError("Choose valid staff account filters.")
    search = filters.search.casefold()
    matching = [
        account for account in accounts
        if (
            not search
            or search in (account.get("display_name") or "").casefold()
            or search in account["username"].casefold()
        )
        and (not filters.status or _staff_account_status(account) == filters.status)
    ]
    if filters.sort == "oldest":
        matching.sort(key=lambda account: (account["created_at"], account["id"]))
    elif filters.sort in {"name", "name_desc"}:
        matching.sort(key=lambda account: (
            (account.get("display_name") or account["username"]).casefold(), account["id"],
        ), reverse=filters.sort == "name_desc")
    elif filters.sort in {"username", "username_desc"}:
        matching.sort(
            key=lambda account: (account["username"].casefold(), account["id"]),
            reverse=filters.sort == "username_desc",
        )
    elif filters.sort in {"status", "status_desc"}:
        status_order = {"inactive": 0, "pending": 1, "ready": 2}
        matching.sort(
            key=lambda account: (status_order[_staff_account_status(account)], account["id"]),
            reverse=filters.sort == "status_desc",
        )
    else:
        matching.sort(key=lambda account: (account["created_at"], account["id"]), reverse=True)
    return matching


def staff_account_status_counts(accounts):
    """Return unfiltered account totals for the three directory status cards."""
    counts = {"ready": 0, "pending": 0, "inactive": 0}
    for account in accounts:
        counts[_staff_account_status(account)] += 1
    return counts


def prepare_temporary_password_email(address, confirmation, verified_in_person):
    """Validate a one-time recipient without storing it on the staff account."""
    if verified_in_person != "yes":
        raise StaffAccountInputError(
            "Confirm that the staff member provided this email address in person."
        )
    address_text = address.strip() if isinstance(address, str) else ""
    confirmation_text = confirmation.strip() if isinstance(confirmation, str) else ""
    if not address_text or address_text.lower() != confirmation_text.lower():
        raise StaffAccountInputError("Enter the same staff email address in both fields.")
    try:
        load_email_settings()
        build_staff_login_link()
        return check_recipient(address_text)
    except EmailDeliveryError as exc:
        raise StaffAccountInputError(str(exc)) from None


def temporary_password_cipher():
    """Require a dedicated encryption key; never fall back to the session key."""
    key = current_app.config.get("STAFF_TEMP_PASSWORD_KEY")
    if not key:
        raise RuntimeError("Staff temporary password encryption is not configured.")
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise RuntimeError("Staff temporary password encryption is not configured.") from None


def get_staff_accounts():
    """Return the staff directory with dates in Philippine time."""
    with get_connection() as connection:
        accounts = staff_users.list_staff_accounts(connection)
    for account in accounts:
        account["created_at"] = account["created_at"].astimezone(
            timezone(timedelta(hours=8)),
        )
    return accounts


def get_staff_account_dashboard_summary():
    """Return reconciled aggregate account counts for the admin dashboard."""
    with get_connection() as connection:
        summary = dict(staff_users.count_staff_account_statuses(connection))
    if (
        summary["total"] != summary["active"] + summary["inactive"]
        or summary["active"] != summary["pending_setup"] + summary["setup_complete"]
    ):
        raise RuntimeError("Staff account summary counts do not reconcile.")
    return summary


def new_temporary_credentials():
    """Create a login hash and an encrypted copy of one new password."""
    password = token_urlsafe(TEMPORARY_PASSWORD_BYTES)
    encrypted = temporary_password_cipher().encrypt(password.encode("utf-8")).decode("ascii")
    return password, generate_password_hash(password, method="scrypt"), encrypted


def create_staff_account(display_name, username):
    """Store a login hash and an encrypted initial password for pending staff."""
    display_name = " ".join(display_name.split())
    username = username.strip().lower()
    if not 1 <= len(display_name) <= 120:
        raise StaffAccountInputError("Enter a staff name between 1 and 120 characters.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise StaffAccountInputError(
            "Use 3–64 username characters: lowercase letters, numbers, dots, dashes or underscores."
        )

    temporary_password, password_hash, encrypted_password = new_temporary_credentials()
    with get_connection() as connection:
        staff_users.insert_staff(
            connection, display_name, username, password_hash, encrypted_password,
        )
    return {
        "display_name": display_name,
        "username": username,
        "temporary_password": temporary_password,
    }


def reveal_temporary_password(user_id):
    """Reveal only an active staff account's still-current initial password."""
    if user_id is None or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        with connection.transaction():
            account = staff_users.find_temporary_password(connection, user_id)
            if not account or not account["temporary_password_encrypted"]:
                raise StaffAccountInputError(
                    "The temporary password is no longer available for this account."
                )
            try:
                password = temporary_password_cipher().decrypt(
                    account["temporary_password_encrypted"],
                ).decode("utf-8")
            except (InvalidToken, UnicodeError):
                raise RuntimeError("The temporary password could not be decrypted.") from None
            if not check_password_hash(account["password_hash"], password):
                raise RuntimeError("The stored temporary password is no longer current.")
    return {
        "id": account["id"],
        "display_name": account["display_name"] or account["username"],
        "username": account["username"],
        "temporary_password": password,
    }


def get_staff_account_to_deactivate(user_id):
    """Show the account and unfinished review count before confirmation."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        account = staff_users.find_active_staff(connection, user_id)
        if account:
            account["active_reviews"] = staff_users.count_active_assigned_reviews(
                connection, user_id,
            )
    if not account:
        raise StaffAccountInputError("This staff account is no longer active.")
    return account


def deactivate_staff_account(user_id):
    """Revoke staff access only when no active review remains assigned."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        with connection.transaction():
            account = staff_users.find_active_staff(connection, user_id, lock=True)
            if not account:
                raise StaffAccountInputError("This staff account is no longer active.")
            active_reviews = staff_users.count_active_assigned_reviews(connection, user_id)
            if active_reviews:
                raise StaffAccountHasActiveReviews(
                    "Reassign or close this staff member's active reviews before deactivating."
                )
            staff_users.deactivate_staff(connection, user_id)


def get_staff_account_for_recovery(user_id, *, reactivate):
    """Return an inactive reactivation target or an active reset target."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        account = staff_users.find_staff_for_recovery(
            connection, user_id, active=not reactivate,
        )
    if not account:
        raise StaffAccountInputError("This staff account is not available for this action.")
    return account


def recover_staff_account(user_id, *, reactivate, administrator_id=None,
                          delivery_method=None):
    """Reset or reactivate staff with a fresh temporary password."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    if ((administrator_id is None) != (delivery_method is None)
            or delivery_method not in (None, "email", "direct_handoff")):
        raise StaffAccountInputError("Choose a valid password-delivery method.")
    with get_connection() as connection:
        with connection.transaction():
            account = staff_users.find_staff_for_recovery(
                connection, user_id, active=not reactivate, lock=True,
            )
            if not account:
                raise StaffAccountInputError("This staff account is not available for this action.")
            password, password_hash, encrypted = new_temporary_credentials()
            staff_users.replace_staff_password(
                connection, user_id, password_hash, encrypted, reactivate=reactivate,
            )
            completed_requests = []
            if administrator_id is not None:
                completed_requests = staff_password_resets.complete_pending(
                    connection, user_id, administrator_id, delivery_method,
                )
    return {
        "id": account["id"], "display_name": account["display_name"] or account["username"],
        "username": account["username"], "temporary_password": password,
        "completed_request_ids": [item["id"] for item in completed_requests],
    }
