"""Migration 0021 staff access rules in an isolated synthetic test schema."""

import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
PASSWORD_HASH = "scrypt:x$y$z"
ALL_PERMISSIONS = {
    "dashboard.view",
    "households.view",
    "assessments.view",
    "reviews.view",
    "reviews.manage",
    "submissions.view",
    "submissions.manage",
}


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for staff access tests.")
class StaffAccessPermissionMigrationTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.schema = f"staff_access_{uuid4().hex}"
        self.connection = psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", autocommit=True,
        )
        self.addCleanup(self._drop_schema)
        self.connection.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)),
        )
        self.connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)),
        )
        self.connection.execute(
            (ROOT / "migrations/0002_staff_authentication.sql").read_text("utf-8"),
        )
        self.admin_id = self._insert_user("existing_admin", "system_administrator")
        self.active_staff_id = self._insert_user("existing_staff", "lgu_staff")
        self.inactive_staff_id = self._insert_user(
            "inactive_staff", "lgu_staff", active=False,
        )
        self.connection.execute(
            (ROOT / "migrations/0021_staff_access_permissions.sql").read_text("utf-8"),
        )

    def _drop_schema(self):
        self.connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.close()

    def _insert_user(self, username, role, *, active=True):
        return self.connection.execute(
            """INSERT INTO staff_users (username, password_hash, role, is_active)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (username, PASSWORD_HASH, role, active),
        ).fetchone()[0]

    def _permissions(self, staff_id):
        return {row[0] for row in self.connection.execute(
            """SELECT permission_code FROM staff_user_permissions
               WHERE staff_id = %s""",
            (staff_id,),
        ).fetchall()}

    def _grant(self, staff_id, permission, actor=None):
        self.connection.execute(
            """INSERT INTO staff_user_permissions
               (staff_id, permission_code, granted_by, grant_source)
               VALUES (%s, %s, %s, 'administrator')""",
            (staff_id, permission, actor or self.admin_id),
        )

    def test_catalog_and_existing_staff_backfill_preserve_current_access(self):
        catalog = {row[0] for row in self.connection.execute(
            "SELECT code FROM staff_permissions",
        ).fetchall()}
        self.assertEqual(catalog, ALL_PERMISSIONS)
        self.assertEqual(self._permissions(self.active_staff_id), ALL_PERMISSIONS)
        self.assertEqual(self._permissions(self.inactive_staff_id), ALL_PERMISSIONS)
        self.assertEqual(self._permissions(self.admin_id), set())

    def test_new_staff_starts_with_dashboard_only_and_admin_gets_no_grants(self):
        staff_id = self._insert_user("new_staff", "lgu_staff")
        second_admin = self._insert_user("second_admin", "system_administrator")
        self.assertEqual(self._permissions(staff_id), {"dashboard.view"})
        self.assertEqual(self._permissions(second_admin), set())
        row = self.connection.execute(
            """SELECT grant_source, granted_by FROM staff_user_permissions
               WHERE staff_id = %s AND permission_code = 'dashboard.view'""",
            (staff_id,),
        ).fetchone()
        self.assertEqual(row, ("account_default", None))

    def test_only_active_administrator_can_grant_staff_permissions(self):
        staff_id = self._insert_user("limited_staff", "lgu_staff")
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(staff_id, "households.view", actor=staff_id)
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(self.admin_id, "households.view")
        self.connection.execute(
            "UPDATE staff_users SET is_active = false WHERE id = %s", (self.admin_id,),
        )
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(staff_id, "households.view")

    def test_dashboard_and_permission_dependencies_are_enforced(self):
        staff_id = self._insert_user("dependency_staff", "lgu_staff")
        with self.assertRaises(psycopg.DatabaseError):
            self.connection.execute(
                """DELETE FROM staff_user_permissions
                   WHERE staff_id = %s AND permission_code = 'dashboard.view'""",
                (staff_id,),
            )
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(staff_id, "reviews.view")
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(staff_id, "reviews.manage")
        with self.assertRaises(psycopg.DatabaseError):
            self._grant(staff_id, "submissions.manage")

        with self.connection.transaction():
            for permission in ("assessments.view", "reviews.view", "reviews.manage"):
                self._grant(staff_id, permission)
        self.assertTrue(
            {"assessments.view", "reviews.view", "reviews.manage"}
            <= self._permissions(staff_id),
        )

    def test_permission_events_require_admin_and_are_append_only(self):
        self.connection.execute(
            """INSERT INTO staff_permission_events
               (staff_id, permission_code, action, changed_by)
               VALUES (%s, 'households.view', 'granted', %s)""",
            (self.active_staff_id, self.admin_id),
        )
        event_id = self.connection.execute(
            "SELECT id FROM staff_permission_events",
        ).fetchone()[0]
        with self.assertRaises(errors.RaiseException):
            self.connection.execute(
                "UPDATE staff_permission_events SET action = 'revoked' WHERE id = %s",
                (event_id,),
            )
        with self.assertRaises(errors.RaiseException):
            self.connection.execute(
                "DELETE FROM staff_permission_events WHERE id = %s", (event_id,),
            )
        with self.assertRaises(psycopg.DatabaseError):
            self.connection.execute(
                """INSERT INTO staff_permission_events
                   (staff_id, permission_code, action, changed_by)
                   VALUES (%s, 'households.view', 'revoked', %s)""",
                (self.active_staff_id, self.active_staff_id),
            )


if __name__ == "__main__":
    unittest.main()
