"""Staff forgot-password requests in an isolated synthetic PostgreSQL schema."""

import os
from pathlib import Path
import re
from secrets import token_urlsafe
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from werkzeug.security import generate_password_hash

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for staff reset tests.")
class StaffPasswordResetRequestTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.schema = f"staff_reset_request_{uuid4().hex}"
        self.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.addCleanup(self._drop_schema)
            with connection.transaction():
                connection.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)),
                )
                for migration in (
                    "0002_staff_authentication.sql", "0009_staff_display_name.sql",
                    "0010_staff_password_change_required.sql",
                    "0011_staff_temporary_password.sql",
                    "0020_staff_password_reset_requests.sql",
                ):
                    connection.execute((ROOT / "migrations" / migration).read_text("utf-8"))

        self.password = token_urlsafe(32)
        with psycopg.connect(self.url) as connection:
            for display_name, username, role, active in (
                ("Synthetic Administrator", "synthetic_admin", "system_administrator", True),
                ("Active Staff", "active_staff", "lgu_staff", True),
                ("Inactive Staff", "inactive_staff", "lgu_staff", False),
            ):
                connection.execute(
                    """INSERT INTO staff_users
                       (display_name, username, password_hash, role, is_active)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (display_name, username, generate_password_hash(self.password), role, active),
                )

        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": self.url,
            "STAFF_TEMP_PASSWORD_KEY": Fernet.generate_key().decode("ascii"),
            "PUBLIC_BASE_URL": "https://tanglaw.example.test",
            "SMTP_HOST": "smtp.example.test", "SMTP_PORT": "587",
            "SMTP_SECURITY": "starttls", "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "synthetic-smtp-password",
            "SMTP_FROM": "Tanglaw-Buhay <sender@example.test>",
            "SMTP_RECIPIENT_MODE": "any", "SMTP_ALLOWED_RECIPIENTS": "",
        })
        self.client = self.app.test_client()

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _csrf(self, path="/forgot-password", client=None):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def _request(self, username, client=None):
        client = client or self.client
        return client.post("/forgot-password", data={
            "csrf_token": self._csrf(client=client), "username": username,
        })

    def _login(self, username="synthetic_admin", client=None):
        client = client or self.client
        response = client.post("/login", data={
            "csrf_token": self._csrf("/login", client),
            "username": username, "password": self.password,
        })
        self.assertEqual(response.status_code, 302)
        return client

    def _request_rows(self):
        with psycopg.connect(self.url) as connection:
            return connection.execute(
                """SELECT status, request_count, delivery_method, email_accepted
                   FROM staff_password_reset_requests ORDER BY id"""
            ).fetchall()

    def test_login_links_to_public_neutral_password_help(self):
        login = self.client.get("/login")
        self.assertIn('href="/forgot-password"', login.text)
        page = self.client.get("/forgot-password")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Forgot your password?", page.text)
        self.assertIn('name="username"', page.text)
        self.assertEqual(self.client.post(
            "/forgot-password", data={"username": "active_staff"},
        ).status_code, 400)

    def test_wrong_admin_and_inactive_usernames_use_same_response_without_requests(self):
        messages = []
        for username in ("misspelled_staff", "synthetic_admin", "inactive_staff"):
            response = self._request(username)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.location, "/forgot-password?sent=1")
            page = self.client.get(response.location)
            self.assertEqual(page.status_code, 200)
            self.assertIn(
                "If the username belongs to an active LGU Staff account", page.text,
            )
            messages.append(re.sub(r'value="[^"]+"', 'value="TOKEN"', page.text))
        self.assertTrue(all(message == messages[0] for message in messages))
        self.assertEqual(self._request_rows(), [])

    def test_valid_username_is_normalized_and_repeated_requests_are_deduplicated(self):
        for username in ("  Active_Staff  ", "ACTIVE_STAFF"):
            self.assertEqual(self._request(username).status_code, 303)
        self.assertEqual(self._request_rows(), [("pending", 2, None, None)])

    def test_browser_session_limit_silently_stops_after_five_requests(self):
        for _ in range(7):
            self.assertEqual(self._request("active_staff").status_code, 303)
        self.assertEqual(self._request_rows(), [("pending", 5, None, None)])

    def test_admin_sees_pending_request_and_staff_cannot_open_admin_page(self):
        self._request("active_staff")
        admin = self._login()
        page = admin.get("/administration/accounts/password-reset-requests")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Password Reset Requests", page.text)
        self.assertIn("Active Staff", page.text)
        self.assertIn("active_staff", page.text)
        self.assertIn("Reset password", page.text)
        self.assertIn('href="/administration/accounts/', page.text)
        with patch("routes.main.active_dataset_exists", return_value=True), \
                patch("routes.main.get_active_dashboard_summary", return_value=None):
            dashboard = admin.get("/administration")
        self.assertIn("1 password reset request waiting", dashboard.text)

        staff = self._login("active_staff", self.app.test_client())
        self.assertEqual(
            staff.get("/administration/accounts/password-reset-requests").status_code, 403,
        )

    def test_direct_reset_completes_request_and_preserves_audit_history(self):
        self._request("active_staff")
        admin = self._login()
        with psycopg.connect(self.url) as connection:
            staff_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'active_staff'",
            ).fetchone()[0]
        url = f"/administration/accounts/{staff_id}/reset-password?source=requests"
        token = self._csrf(url, admin)
        response = admin.post(url, data={
            "csrf_token": token, "delivery_method": "display",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-return-url="/administration/accounts/password-reset-requests"',
                      response.text)
        self.assertEqual(self._request_rows(), [("completed", 1, "direct_handoff", None)])
        history = admin.get("/administration/accounts/password-reset-requests")
        self.assertIn("Direct handoff", history.text)
        self.assertIn("Synthetic Administrator", history.text)
        with psycopg.connect(self.url) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute("DELETE FROM staff_password_reset_requests")

    def test_successful_email_reset_records_acceptance_without_storing_address(self):
        self._request("active_staff")
        admin = self._login()
        with psycopg.connect(self.url) as connection:
            staff_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'active_staff'",
            ).fetchone()[0]
        url = f"/administration/accounts/{staff_id}/reset-password?source=requests"
        with patch("routes.main.send_staff_temporary_password_email") as send_email:
            response = admin.post(url, data={
                "csrf_token": self._csrf(url, admin), "delivery_method": "email",
                "recipient_email": "Active.Staff@Example.com",
                "confirm_recipient_email": "active.staff@example.com",
                "verified_in_person": "yes",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn("email server accepted", response.text)
        send_email.assert_called_once()
        self.assertEqual(self._request_rows(), [("completed", 1, "email", True)])
        self.assertNotIn("active.staff@example.com", response.text.lower())
        with psycopg.connect(self.url) as connection:
            columns = {row[0] for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema = current_schema()
                     AND table_name = 'staff_password_reset_requests'"""
            ).fetchall()}
        self.assertNotIn("email", columns)
        self.assertNotIn("temporary_password", columns)


if __name__ == "__main__":
    unittest.main()
