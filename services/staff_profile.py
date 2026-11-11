"""Self-service credential changes for a signed-in LGU Staff account."""

from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users


class StaffProfileInputError(ValueError):
    """The staff password form needs correction."""


def change_staff_password(user, current_password, new_password, confirmation):
    """Verify the current password, save a new hash, and revoke older sessions."""
    if not 1 <= len(current_password) <= 1024:
        raise StaffProfileInputError("Enter your current password.")
    if not 12 <= len(new_password) <= 1024:
        raise StaffProfileInputError("Use a new password of at least 12 characters.")
    if new_password != confirmation:
        raise StaffProfileInputError("The new passwords do not match.")

    with get_connection() as connection:
        with connection.transaction():
            account = staff_users.find_for_password_change(connection, user["id"])
            if (not account or not account["is_active"]
                    or account["role"] != "lgu_staff"
                    or account["must_change_password"]
                    or account["session_version"] != user["session_version"]):
                raise StaffProfileInputError("Your sign-in has changed. Sign in again.")
            if not check_password_hash(account["password_hash"], current_password):
                raise StaffProfileInputError("The current password is incorrect.")
            if check_password_hash(account["password_hash"], new_password):
                raise StaffProfileInputError(
                    "Choose a password different from your current password."
                )
            updated = staff_users.update_staff_password(
                connection, user["id"],
                generate_password_hash(new_password, method="scrypt"),
            )
            if not updated:
                raise StaffProfileInputError("Your password could not be changed.")
            return updated
