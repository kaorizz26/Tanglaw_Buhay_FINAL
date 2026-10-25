"""Administrator account creation in an isolated synthetic test schema."""

import os
from pathlib import Path
import re
from secrets import token_urlsafe
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for account creation tests.")
class StaffAccountCreationTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.schema = f"account_create_{uuid4().hex}"
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
                for migration in ("0002_staff_authentication.sql", "0009_staff_display_name.sql",
                                  "0010_staff_password_change_required.sql"):
                    connection.execute((ROOT / "migrations" / migration).read_text())
        self.password = token_urlsafe(32)
        with psycopg.connect(self.url) as connection:
            for username, role in (("synthetic_admin", "system_administrator"),
                                   ("synthetic_staff", "lgu_staff")):
                connection.execute(
                    """INSERT INTO staff_users (username, password_hash, role)
                       VALUES (%s, %s, %s)""",
                    (username, generate_password_hash(self.password, method="scrypt"), role),
                )
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": self.url,
        })
        self.client = self.app.test_client()

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def _login(self, username="synthetic_admin", client=None):
        client = client or self.client
        response = client.post("/login", data={
            "username": username, "password": self.password,
            "csrf_token": self._csrf(client, "/login"),
        })
        self.assertEqual(response.status_code, 302)

    def test_admin_creates_only_staff_and_sees_initial_password_once(self):
        self._login()
        token = self._csrf(self.client, "/administration/accounts")
        page = self.client.get("/administration/accounts")
        self.assertIn("Create staff account", page.text)
        self.assertIn('src="/static/js/staff-accounts.js"', page.text)
        response = self.client.post("/administration/accounts", data={
            "csrf_token": token, "display_name": "  Ana   Dela Cruz  ",
            "username": "Ana.Dela.Cruz", "role": "system_administrator",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Account created", response.text)
        self.assertIn("Ana Dela Cruz", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        initial_password = re.search(r"<dd><code>([^<]+)</code></dd>", response.text).group(1)
        with psycopg.connect(self.url) as connection:
            account = connection.execute(
                """SELECT display_name, username, password_hash, role, is_active,
                          must_change_password FROM staff_users
                   WHERE username = 'ana.dela.cruz'""",
            ).fetchone()
        self.assertEqual(account[:2], ("Ana Dela Cruz", "ana.dela.cruz"))
        self.assertNotEqual(account[2], initial_password)
        self.assertTrue(check_password_hash(account[2], initial_password))
        self.assertEqual(account[3:], ("lgu_staff", True, True))
        self.assertNotIn(initial_password, self.client.get("/administration/accounts").text)

    def test_creation_rejects_duplicate_invalid_and_unauthorized_requests(self):
        self.assertEqual(self.client.post("/administration/accounts").location, "/login")
        self._login()
        self.assertEqual(self.client.post("/administration/accounts", data={
            "display_name": "Ana Dela Cruz", "username": "ana.dela.cruz",
        }).status_code, 400)
        token = self._csrf(self.client, "/administration/accounts")
        for display_name, username in (("", "ana.dela.cruz"),
                                       ("Ana Dela Cruz", "bad username"),
                                       ("Ana Dela Cruz", "a")):
            with self.subTest(username=username):
                response = self.client.post("/administration/accounts", data={
                    "csrf_token": token, "display_name": display_name,
                    "username": username,
                })
                self.assertEqual(response.status_code, 400)
        duplicate = self.client.post("/administration/accounts", data={
            "csrf_token": token, "display_name": "Another Person",
            "username": "Synthetic_Admin",
        })
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already in use", duplicate.text)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            blocked = self.client.post("/administration/accounts", data={
                "csrf_token": token, "display_name": "Another Person",
                "username": "another.person",
            })
        self.assertEqual(blocked.status_code, 403)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM staff_users").fetchone()[0], 2)

        staff_client = self.app.test_client()
        self._login("synthetic_staff", staff_client)
        self.assertEqual(staff_client.get("/administration/accounts").status_code, 403)
        self.assertEqual(staff_client.post("/administration/accounts", data={
            "csrf_token": token, "display_name": "Another Person", "username": "another.person",
        }).status_code, 403)

    def test_new_staff_sign_in_requires_password_setup(self):
        self._login()
        created = self.client.post("/administration/accounts", data={
            "csrf_token": self._csrf(self.client, "/administration/accounts"),
            "display_name": "Ana Dela Cruz", "username": "ana.dela.cruz",
        })
        self.assertEqual(created.status_code, 200)
        initial_password = re.search(r"<dd><code>([^<]+)</code></dd>", created.text).group(1)
        staff_client = self.app.test_client()
        sign_in = staff_client.post("/login", data={
            "csrf_token": self._csrf(staff_client, "/login"),
            "username": "ana.dela.cruz", "password": initial_password,
        })
        self.assertEqual(sign_in.location, "/change-password")
        old_cookie = staff_client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        for path in ("/", "/households", "/assessments", "/login"):
            self.assertEqual(staff_client.get(path).location, "/change-password")
        self.assertEqual(staff_client.post("/administration/accounts", data={}).location,
                         "/change-password")
        setup = staff_client.get("/change-password")
        self.assertEqual(setup.status_code, 200)
        self.assertIn("Set your password", setup.text)
        self.assertNotIn(initial_password, setup.text)
        self.assertEqual(setup.headers["Cache-Control"], "no-store")
        token = self._csrf(staff_client, "/change-password")
        new_password = token_urlsafe(32)
        self.assertEqual(staff_client.post("/change-password", data={
            "current_password": initial_password, "new_password": new_password,
            "confirm_password": new_password,
        }).status_code, 400)
        for current, new, confirm, message in (
            ("incorrect", new_password, new_password, "current password is incorrect"),
            (initial_password, "short", "short", "at least 12 characters"),
            (initial_password, new_password, "different", "do not match"),
            (initial_password, initial_password, initial_password, "different from the initial"),
        ):
            with self.subTest(message=message):
                response = staff_client.post("/change-password", data={
                    "csrf_token": token, "current_password": current,
                    "new_password": new, "confirm_password": confirm,
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.text)
                self.assertNotIn(initial_password, response.text)

        saved = staff_client.post("/change-password", data={
            "csrf_token": token, "current_password": initial_password,
            "new_password": new_password, "confirm_password": new_password,
        })
        self.assertEqual(saved.location, "/")
        with psycopg.connect(self.url) as connection:
            password_hash, must_change, version = connection.execute(
                """SELECT password_hash, must_change_password, session_version
                   FROM staff_users WHERE username = 'ana.dela.cruz'""",
            ).fetchone()
        self.assertFalse(must_change)
        self.assertEqual(version, 2)
        self.assertTrue(check_password_hash(password_hash, new_password))
        self.assertFalse(check_password_hash(password_hash, initial_password))
        self.assertEqual(staff_client.get("/").status_code, 200)
        self.assertEqual(staff_client.get("/change-password").location, "/")
        replay = self.app.test_client()
        replay.set_cookie(self.app.config["SESSION_COOKIE_NAME"], old_cookie)
        self.assertEqual(replay.get("/").location, "/login")
        self.assertEqual(staff_client.post("/logout", data={
            "csrf_token": self._csrf(staff_client, "/logout"),
        }).location, "/login")
        self.assertEqual(staff_client.post("/login", data={
            "csrf_token": self._csrf(staff_client, "/login"),
            "username": "ana.dela.cruz", "password": initial_password,
        }).status_code, 401)
        self.assertEqual(staff_client.post("/login", data={
            "csrf_token": self._csrf(staff_client, "/login"),
            "username": "ana.dela.cruz", "password": new_password,
        }).location, "/")

    def test_flagged_staff_can_sign_out_before_setting_password(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """UPDATE staff_users SET must_change_password = true
                   WHERE username = 'synthetic_staff'""",
            )
        staff_client = self.app.test_client()
        self._login("synthetic_staff", staff_client)
        self.assertEqual(staff_client.get("/").location, "/change-password")
        token = self._csrf(staff_client, "/change-password")
        self.assertEqual(staff_client.post("/logout", data={
            "csrf_token": token,
        }).location, "/login")
        self.assertEqual(staff_client.get("/change-password").location, "/login")


if __name__ == "__main__":
    unittest.main()
