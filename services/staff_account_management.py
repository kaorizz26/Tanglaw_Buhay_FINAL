"""Validation and credential creation for administrator-created staff accounts."""

import re
from datetime import timedelta, timezone
from secrets import choice

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users


class StaffAccountInputError(ValueError):
    """A staff account request cannot be completed in its current state."""


_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


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

    temporary_password = "".join(choice(_TEMP_PASSWORD_ALPHABET) for _ in range(16))
    encrypted_password = temporary_password_cipher().encrypt(
        temporary_password.encode("utf-8"),
    ).decode("ascii")
    password_hash = generate_password_hash(temporary_password, method="scrypt")
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


def get_deletable_staff_account(user_id):
    """Find the staff account named on a delete confirmation page."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        account = staff_users.find_deletable_staff(connection, user_id)
    if not account:
        raise StaffAccountInputError("This staff account is no longer available.")
    return account


def delete_staff_account(user_id):
    """Revoke a staff login without breaking attributed records."""
    if not isinstance(user_id, int) or not 0 < user_id <= 9223372036854775807:
        raise StaffAccountInputError("Choose a valid staff account.")
    with get_connection() as connection:
        deleted = staff_users.delete_staff(connection, user_id)
    if not deleted:
        raise StaffAccountInputError("This staff account is no longer available.")
