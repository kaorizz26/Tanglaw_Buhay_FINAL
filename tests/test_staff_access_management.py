"""Service behavior for validated and audited LGU Staff access changes."""

import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from services.staff_access import (
    ACCESS_PRESETS,
    StaffAccessInputError,
    get_staff_access,
    get_staff_access_history,
    update_staff_access,
    validate_permission_selection,
)


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
PASSWORD_HASH = "scrypt:x$y$z"


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for staff access tests.")
class StaffAccessManagementTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.schema = f"staff_access_service_{uuid4().hex}"
        self.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )
        with psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)),
            )
            self.addCleanup(self._drop_schema)
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)),
            )
            for migration in (
                "0002_staff_authentication.sql",
                "0009_staff_display_name.sql",
                "0021_staff_access_permissions.sql",
            ):
                connection.execute((ROOT / "migrations" / migration).read_text("utf-8"))
        self.admin_id = self._insert_user(
            "Access Administrator", "access_admin", "system_administrator",
        )
        self.staff_id = self._insert_user(
            "Limited Staff", "limited_staff", "lgu_staff",
        )

    def _drop_schema(self):
        with psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _connection(self):
        return psycopg.connect(self.url)

    def _insert_user(self, display_name, username, role, *, active=True):
        with self._connection() as connection:
            return connection.execute(
                """INSERT INTO staff_users
                   (display_name, username, password_hash, role, is_active)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (display_name, username, PASSWORD_HASH, role, active),
            ).fetchone()[0]

    def _session_version(self):
        with self._connection() as connection:
            return connection.execute(
                "SELECT session_version FROM staff_users WHERE id = %s",
                (self.staff_id,),
            ).fetchone()[0]

    def test_read_returns_ordered_catalog_and_dashboard_default(self):
        result = get_staff_access(self.staff_id, connection_factory=self._connection)
        self.assertEqual(result["account"]["display_name"], "Limited Staff")
        self.assertEqual(result["selected"], {"dashboard.view"})
        self.assertEqual(result["permissions"][0]["code"], "dashboard.view")
        self.assertTrue(result["permissions"][0]["granted"])
        self.assertFalse(result["changed"])

    def test_change_is_audited_and_revokes_sessions_once(self):
        starting_version = self._session_version()
        selection = ACCESS_PRESETS["household_viewer"]
        result = update_staff_access(
            self.admin_id, self.staff_id, selection,
            connection_factory=self._connection,
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["selected"], selection)
        self.assertEqual(self._session_version(), starting_version + 1)

        history = get_staff_access_history(
            self.staff_id, connection_factory=self._connection,
        )
        self.assertEqual(len(history["events"]), 1)
        self.assertEqual(history["events"][0]["permission_code"], "households.view")
        self.assertEqual(history["events"][0]["action"], "granted")
        self.assertEqual(history["events"][0]["administrator_name"], "Access Administrator")

        unchanged = update_staff_access(
            self.admin_id, self.staff_id, selection,
            connection_factory=self._connection,
        )
        self.assertFalse(unchanged["changed"])
        self.assertEqual(self._session_version(), starting_version + 1)
        self.assertEqual(len(get_staff_access_history(
            self.staff_id, connection_factory=self._connection,
        )["events"]), 1)

        update_staff_access(
            self.admin_id, self.staff_id, ACCESS_PRESETS["dashboard_only"],
            connection_factory=self._connection,
        )
        latest = get_staff_access_history(
            self.staff_id, connection_factory=self._connection,
        )["events"][0]
        self.assertEqual((latest["permission_code"], latest["action"]),
                         ("households.view", "revoked"))

    def test_presets_and_custom_dependencies_are_validated(self):
        for selection in ACCESS_PRESETS.values():
            self.assertEqual(validate_permission_selection(selection), selection)
        invalid = (
            set(),
            {"dashboard.view", "reviews.view"},
            {"dashboard.view", "reviews.manage"},
            {"dashboard.view", "submissions.manage"},
            {"dashboard.view", "unknown.permission"},
            ["dashboard.view", "dashboard.view"],
            "dashboard.view",
        )
        for selection in invalid:
            with self.subTest(selection=selection):
                with self.assertRaises(StaffAccessInputError):
                    validate_permission_selection(selection)

    def test_invalid_update_leaves_access_sessions_and_history_unchanged(self):
        starting_version = self._session_version()
        with self.assertRaises(StaffAccessInputError):
            update_staff_access(
                self.admin_id, self.staff_id,
                {"dashboard.view", "reviews.manage"},
                connection_factory=self._connection,
            )
        self.assertEqual(self._session_version(), starting_version)
        self.assertEqual(get_staff_access(
            self.staff_id, connection_factory=self._connection,
        )["selected"], {"dashboard.view"})
        self.assertEqual(get_staff_access_history(
            self.staff_id, connection_factory=self._connection,
        )["events"], [])

    def test_inactive_or_non_admin_accounts_cannot_change_access(self):
        inactive_id = self._insert_user(
            "Inactive Staff", "inactive_staff_access", "lgu_staff", active=False,
        )
        with self.assertRaises(StaffAccessInputError):
            update_staff_access(
                self.admin_id, inactive_id, ACCESS_PRESETS["dashboard_only"],
                connection_factory=self._connection,
            )
        with self.assertRaises(StaffAccessInputError):
            update_staff_access(
                self.staff_id, self.staff_id, ACCESS_PRESETS["dashboard_only"],
                connection_factory=self._connection,
            )


if __name__ == "__main__":
    unittest.main()
