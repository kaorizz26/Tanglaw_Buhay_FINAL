"""Credential-change rules for the administrator profile."""

import unittest
from unittest.mock import MagicMock, patch

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

from services.admin_profile import (
    AdminProfileInputError, change_admin_password, change_admin_username,
)


class AdminProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.current_password = "synthetic current password"
        self.user = {
            "id": 7,
            "username": "synthetic_admin",
            "role": "system_administrator",
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
        self.updated = {
            "id": 7,
            "role": "system_administrator",
            "session_version": 4,
        }

    def test_username_is_normalized_and_saved_immediately(self):
        with patch("services.admin_profile.get_connection", return_value=self.connection), \
                patch("services.admin_profile.staff_users.find_for_profile_change",
                      return_value=self.account), \
                patch("services.admin_profile.staff_users.update_admin_username",
                      return_value=self.updated) as update:
            user, username = change_admin_username(self.user, "  New.Admin  ")

        self.assertEqual(user, self.updated)
        self.assertEqual(username, "new.admin")
        update.assert_called_once_with(self.connection, 7, "new.admin")

    def test_username_rejects_invalid_same_and_duplicate_values(self):
        for username in ("bad username", "x", "UPPER CASE"):
            with self.subTest(username=username):
                with self.assertRaises(AdminProfileInputError):
                    change_admin_username(self.user, username)

        with patch("services.admin_profile.get_connection", return_value=self.connection), \
                patch("services.admin_profile.staff_users.find_for_profile_change",
                      return_value=self.account):
            with self.assertRaisesRegex(AdminProfileInputError, "different username"):
                change_admin_username(self.user, "synthetic_admin")

        with patch("services.admin_profile.get_connection", return_value=self.connection), \
                patch("services.admin_profile.staff_users.find_for_profile_change",
                      return_value=self.account), \
                patch("services.admin_profile.staff_users.update_admin_username",
                      side_effect=psycopg.errors.UniqueViolation()):
            with self.assertRaisesRegex(AdminProfileInputError, "already in use"):
                change_admin_username(self.user, "another.admin")

    def test_password_requires_current_password_and_stores_a_new_hash(self):
        captured_hash = None

        def update(connection, user_id, password_hash):
            nonlocal captured_hash
            captured_hash = password_hash
            return self.updated

        new_password = "a new synthetic password"
        with patch("services.admin_profile.get_connection", return_value=self.connection), \
                patch("services.admin_profile.staff_users.find_for_profile_change",
                      return_value=self.account), \
                patch("services.admin_profile.staff_users.update_admin_password",
                      side_effect=update):
            user = change_admin_password(
                self.user, self.current_password, new_password, new_password,
            )

        self.assertEqual(user, self.updated)
        self.assertTrue(check_password_hash(captured_hash, new_password))
        self.assertFalse(check_password_hash(captured_hash, self.current_password))

    def test_password_rejects_bad_credentials_without_updating(self):
        update = MagicMock()
        with patch("services.admin_profile.get_connection", return_value=self.connection), \
                patch("services.admin_profile.staff_users.find_for_profile_change",
                      return_value=self.account), \
                patch("services.admin_profile.staff_users.update_admin_password", update):
            with self.assertRaisesRegex(AdminProfileInputError, "incorrect"):
                change_admin_password(
                    self.user, "wrong password", "a new synthetic password",
                    "a new synthetic password",
                )
        update.assert_not_called()

        for new, confirmation, message in (
                ("short", "short", "at least 12"),
                ("a new synthetic password", "different synthetic value", "do not match"),
                (self.current_password, self.current_password, "different from your current")):
            with self.subTest(message=message), \
                    patch("services.admin_profile.get_connection", return_value=self.connection), \
                    patch("services.admin_profile.staff_users.find_for_profile_change",
                          return_value=self.account), \
                    patch("services.admin_profile.staff_users.update_admin_password"):
                with self.assertRaisesRegex(AdminProfileInputError, message):
                    change_admin_password(
                        self.user, self.current_password, new, confirmation,
                    )


if __name__ == "__main__":
    unittest.main()
