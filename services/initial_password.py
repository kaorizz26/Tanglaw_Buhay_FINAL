"""Complete the required password change for newly created accounts."""

from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users


class InitialPasswordError(ValueError):
    """The password setup form needs correction."""


def set_initial_password(user, current_password, new_password, confirmation):
    """Verify the initial password, replace it, and revoke older sessions."""
    if not 12 <= len(new_password) <= 1024:
        raise InitialPasswordError("Use a new password of at least 12 characters.")
    if new_password != confirmation:
        raise InitialPasswordError("The new passwords do not match.")
    if not 1 <= len(current_password) <= 1024:
        raise InitialPasswordError("Enter your current password.")

    with get_connection() as connection:
        with connection.transaction():
            account = staff_users.find_for_password_change(connection, user["id"])
            if (not account or not account["is_active"]
                    or not account["must_change_password"]
                    or account["session_version"] != user["session_version"]):
                raise InitialPasswordError("Your sign-in has changed. Sign in again.")
            if not check_password_hash(account["password_hash"], current_password):
                raise InitialPasswordError("The current password is incorrect.")
            if check_password_hash(account["password_hash"], new_password):
                raise InitialPasswordError("Choose a password different from the initial one.")
            password_hash = generate_password_hash(new_password, method="scrypt")
            return staff_users.complete_initial_password_change(
                connection, user["id"], password_hash,
            )
