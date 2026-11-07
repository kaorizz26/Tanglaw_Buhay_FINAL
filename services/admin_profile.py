"""Immediate credential changes for the signed-in system administrator."""

import re

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users


class AdminProfileInputError(ValueError):
    """An administrator profile form needs correction."""


def _locked_admin(connection, user):
    account = staff_users.find_for_profile_change(connection, user["id"])
    if (not account or not account["is_active"]
            or account["role"] != "system_administrator"
            or account["session_version"] != user["session_version"]):
        raise AdminProfileInputError("Your sign-in has changed. Sign in again.")
    return account


def change_admin_username(user, username):
    """Validate and immediately replace the current administrator username."""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise AdminProfileInputError(
            "Use 3–64 lowercase letters, numbers, dots, dashes or underscores."
        )

    try:
        with get_connection() as connection:
            with connection.transaction():
                account = _locked_admin(connection, user)
                if account["username"] == username:
                    raise AdminProfileInputError("Enter a different username.")
                updated = staff_users.update_admin_username(
                    connection, user["id"], username,
                )
                if not updated:
                    raise AdminProfileInputError("Your username could not be changed.")
                return updated, username
    except psycopg.errors.UniqueViolation:
        raise AdminProfileInputError(
            "That username is already in use. Choose another username."
        ) from None


def change_admin_password(user, current_password, new_password, confirmation):
    """Verify the current password and immediately store a new salted hash."""
    if not 1 <= len(current_password) <= 1024:
        raise AdminProfileInputError("Enter your current password.")
    if not 12 <= len(new_password) <= 1024:
        raise AdminProfileInputError("Use a new password of at least 12 characters.")
    if new_password != confirmation:
        raise AdminProfileInputError("The new passwords do not match.")

    with get_connection() as connection:
        with connection.transaction():
            account = _locked_admin(connection, user)
            if not check_password_hash(account["password_hash"], current_password):
                raise AdminProfileInputError("The current password is incorrect.")
            if check_password_hash(account["password_hash"], new_password):
                raise AdminProfileInputError(
                    "Choose a password different from your current password."
                )
            updated = staff_users.update_admin_password(
                connection, user["id"],
                generate_password_hash(new_password, method="scrypt"),
            )
            if not updated:
                raise AdminProfileInputError("Your password could not be changed.")
            return updated
