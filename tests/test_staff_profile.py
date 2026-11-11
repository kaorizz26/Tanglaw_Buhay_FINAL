"""Self-service password-change rules for LGU Staff."""

import unittest
from unittest.mock import MagicMock, patch

from werkzeug.security import check_password_hash, generate_password_hash

from services.staff_profile import StaffProfileInputError, change_staff_password


class StaffProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.current_password = "synthetic current password"
        self.user = {
            "id": 7,
            "username": "synthetic_staff",
            "role": "lgu_staff",
            "session_version": 3,
        }
        self.account = {
            **self.user,
            "is_active": True,
            "must_change_password": False,
            "password_hash": generate_password_hash(
                self.current_password, method="scrypt",
            ),
        }
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.transaction.return_value.__enter__.return_value = None
        self.updated = {"id": 7, "role": "lgu_staff", "session_version": 4}

    def test_password_is_verified_hashed_and_saved(self):
        captured_hash = None

        def update(connection, user_id, password_hash):
            nonlocal captured_hash
            captured_hash = password_hash
            return self.updated

        new_password = "a new synthetic password"
        with patch("services.staff_profile.get_connection", return_value=self.connection), \
                patch("services.staff_profile.staff_users.find_for_password_change",
                      return_value=self.account), \
                patch("services.staff_profile.staff_users.update_staff_password",
                      side_effect=update) as updater:
            result = change_staff_password(
                self.user, self.current_password, new_password, new_password,
            )

        self.assertEqual(result, self.updated)
        updater.assert_called_once()
        self.assertTrue(check_password_hash(captured_hash, new_password))
        self.assertFalse(check_password_hash(captured_hash, self.current_password))

    def test_invalid_password_entries_do_not_update(self):
        for current, new, confirmation, message in (
            ("", "a new synthetic password", "a new synthetic password", "current"),
            (self.current_password, "short", "short", "at least 12"),
            (self.current_password, "a new synthetic password",
             "different synthetic value", "do not match"),
            ("wrong password", "a new synthetic password",
             "a new synthetic password", "incorrect"),
            (self.current_password, self.current_password,
             self.current_password, "different from your current"),
        ):
            with self.subTest(message=message), \
                    patch("services.staff_profile.get_connection",
                          return_value=self.connection), \
                    patch("services.staff_profile.staff_users.find_for_password_change",
                          return_value=self.account), \
                    patch("services.staff_profile.staff_users.update_staff_password") as update:
                with self.assertRaisesRegex(StaffProfileInputError, message):
                    change_staff_password(self.user, current, new, confirmation)
                update.assert_not_called()

    def test_wrong_role_stale_session_and_initial_setup_are_rejected(self):
        cases = (
            {**self.account, "role": "system_administrator"},
            {**self.account, "session_version": 4},
            {**self.account, "must_change_password": True},
            {**self.account, "is_active": False},
            None,
        )
        for account in cases:
            with self.subTest(account=account), \
                    patch("services.staff_profile.get_connection",
                          return_value=self.connection), \
                    patch("services.staff_profile.staff_users.find_for_password_change",
                          return_value=account), \
                    patch("services.staff_profile.staff_users.update_staff_password") as update:
                with self.assertRaisesRegex(StaffProfileInputError, "sign-in has changed"):
                    change_staff_password(
                        self.user, self.current_password,
                        "a new synthetic password", "a new synthetic password",
                    )
                update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
