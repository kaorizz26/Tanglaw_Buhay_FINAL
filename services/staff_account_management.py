"""Validation and credential creation for administrator-created staff accounts."""

import re
from secrets import token_urlsafe

from werkzeug.security import generate_password_hash

from db import get_connection
from repositories import staff_users


class StaffAccountInputError(ValueError):
    """A staff account field needs correction before creation."""


def create_staff_account(display_name, username):
    """Create LGU Staff with a generated password returned for one-time display."""
    display_name = " ".join(display_name.split())
    username = username.strip().lower()
    if not 1 <= len(display_name) <= 120:
        raise StaffAccountInputError("Enter a staff name between 1 and 120 characters.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise StaffAccountInputError(
            "Use 3–64 username characters: lowercase letters, numbers, dots, dashes or underscores."
        )

    temporary_password = token_urlsafe(24)
    password_hash = generate_password_hash(temporary_password, method="scrypt")
    with get_connection() as connection:
        staff_users.insert_staff(connection, display_name, username, password_hash)
    return {
        "display_name": display_name,
        "username": username,
        "temporary_password": temporary_password,
    }
