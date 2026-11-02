"""Validation and credential creation for administrator-created staff accounts."""

import re
from datetime import timedelta, timezone
from secrets import token_urlsafe

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users


class StaffAccountInputError(ValueError):
    """A staff account request cannot be completed in its current state."""


class StaffAccountHasActiveReviews(StaffAccountInputError):
    """The staff member must be relieved of active reviews first."""


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


def new_temporary_credentials():
    """Create a login hash and an encrypted copy of one new password."""
    password = token_urlsafe(24)
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


def recover_staff_account(user_id, *, reactivate):
    """Reset or reactivate staff with a fresh temporary password."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
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
    return {
        "id": account["id"], "display_name": account["display_name"] or account["username"],
        "username": account["username"], "temporary_password": password,
    }
