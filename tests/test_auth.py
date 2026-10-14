"""Authentication checks with synthetic accounts in isolated local test schemas."""

from datetime import timedelta
from getpass import GetPassWarning
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app
from commands import check_local_bootstrap_database
from services.auth import admin_required, create_initial_admin

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


class AuthenticationConfigurationTests(unittest.TestCase):
    def test_missing_or_short_key_fails_closed_but_health_remains_public(self):
        app = create_app({"TESTING": True, "DATABASE_URL": None})
        client = app.test_client()
        for key in (None, "too-short"):
            app.config["SECRET_KEY"] = key
            with patch("services.auth.get_connection") as connection:
                for path in ("/login", "/households", "/assessments"):
                    self.assertEqual(client.get(path).status_code, 503)
                self.assertEqual(client.get("/test").get_json()["status"], "ok")
                connection.assert_not_called()

    def test_bootstrap_refuses_old_remote_or_implicit_database(self):
        for url in (
            None, "dbname=tanglaw_buhay host=localhost",
            "dbname=tanglaw_buhay_test host=localhost",
            "dbname=tanglaw_buhay_dev host=remote.invalid",
            "dbname=tanglaw_buhay_dev host=localhost hostaddr=192.0.2.1",
            "dbname=tanglaw_buhay_dev host=localhost service=other",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_local_bootstrap_database(url)
        check_local_bootstrap_database("dbname=tanglaw_buhay_dev host=localhost")

    def test_bootstrap_command_accepts_no_password_arguments_and_refuses_echo(self):
        app = create_app({"TESTING": True, "DATABASE_URL": None})
        runner = app.test_cli_runner()
        result = runner.invoke(args=["create-admin", "--password", "unused"])
        self.assertNotEqual(result.exit_code, 0)
        with patch("commands.check_local_bootstrap_database"):
            with patch("commands.getpass", side_effect=GetPassWarning("unsafe echo")):
                with patch("commands.create_initial_admin") as creator:
                    result = runner.invoke(args=["create-admin"], input="synthetic_admin\n")
        creator.assert_not_called()
        self.assertIn("interactive terminal", result.output)
        password = token_urlsafe(32)
        with patch("commands.check_local_bootstrap_database"):
            with patch("commands.getpass", side_effect=[password, password]):
                with patch("commands.create_initial_admin") as creator:
                    result = runner.invoke(args=["create-admin"], input="synthetic_admin\n")
        creator.assert_called_once_with("synthetic_admin", password)
        self.assertEqual(result.exit_code, 0)
        self.assertNotIn(password, result.output)


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic auth tests.")
class StaffAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48),
            "DATABASE_URL": None, "SESSION_COOKIE_SECURE": True,
        })
        # Only a test endpoint exercises the future admin decorator; no admin UI is added.
        @cls.app.get("/synthetic-admin-check")
        @admin_required
        def admin_check():
            return "Authorized"

    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.schema = f"auth_check_{uuid4().hex}"
        self.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            self.assertEqual(connection.info.dbname, "tanglaw_buhay_test")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.addCleanup(self._drop_schema)
            with connection.transaction():
                connection.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)),
                )
                connection.execute(
                    (ROOT / "migrations/0002_staff_authentication.sql").read_text(),
                )
        self.app.config.update(
            DATABASE_URL=self.url, PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        )
        self.password = token_urlsafe(32)
        with psycopg.connect(self.url) as connection:
            for username, role in (("synthetic_admin", "system_administrator"),
                                   ("synthetic_staff", "lgu_staff")):
                connection.execute(
                    """INSERT INTO staff_users (username, password_hash, role)
                       VALUES (%s, %s, %s)""",
                    (username, generate_password_hash(self.password), role),
                )
        self.client = self.app.test_client()

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _token(self, client=None, path="/login"):
        response = (client or self.client).get(path)
        self.assertEqual(response.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def _login(self, username="synthetic_staff", client=None, password=None):
        client = client or self.client
        token = self._token(client)
        return client.post("/login", data={
            "username": username, "password": self.password if password is None else password,
            "csrf_token": token,
        })

    def test_both_roles_login_and_access_minimal_landing(self):
        for username in ("synthetic_staff", "synthetic_admin"):
            client = self.app.test_client()
            response = self._login(username, client)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/")
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            role_label = "System Administrator" if username == "synthetic_admin" else "LGU Staff"
            self.assertIn(role_label, page.text)
            self.assertNotIn(self.password, page.text)
            for path in ("/households", "/households/DEV-0001",
                         "/assessments/DEV-0001?tab=mpi", "/assessments?view=queue"):
                self.assertEqual(client.get(path).status_code, 410)
            # The new page fails safely because this isolated auth schema has no assessments.
            self.assertEqual(client.get("/assessments").status_code, 503)

    def test_anonymous_routes_are_protected_and_health_is_independent(self):
        for path in ("/", "/households", "/households/DEV-0001", "/assessments",
                     "/assessments/DEV-0001", "/logout", "/synthetic-admin-check"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.location, "/login")
            self.assertNotIn("DEV-0001", response.text)
        with patch("services.auth.get_connection", side_effect=AssertionError("No DB for health")):
            self.assertEqual(self.client.get("/test").get_json(),
                             {"status": "ok", "application": "Tanglaw-Buhay"})

    def test_invalid_and_inactive_credentials_have_identical_generic_messages(self):
        bodies = []
        for username, password in (("synthetic_staff", token_urlsafe(20)),
                                   ("unknown_synthetic", self.password)):
            response = self._login(username, password=password)
            self.assertEqual(response.status_code, 401)
            bodies.append(re.search(r'<p role="alert">(.*?)</p>', response.text).group(1))
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE staff_users SET is_active = false")
        response = self._login()
        self.assertEqual(response.status_code, 401)
        self.assertIn(bodies[0], response.text)
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_login_csrf_missing_wrong_cross_browser_and_unicode_are_rejected(self):
        token = self._token()
        other_client = self.app.test_client()
        other_token = self._token(other_client)
        for value in (None, "incorrect", other_token, "\u2603"):
            data = {"username": "synthetic_staff", "password": self.password}
            if value is not None:
                data["csrf_token"] = value
            self.assertEqual(self.client.post("/login", data=data).status_code, 400)
        self.assertEqual(self.client.post("/login", data={
            "username": "synthetic_staff", "password": self.password, "csrf_token": token,
        }).status_code, 302)

    def test_logout_requires_post_and_csrf_and_revokes_replayed_sessions(self):
        self._login()
        old_cookie = self.client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        token = self._token(path="/logout")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 400)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": token}).status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 302)
        replay = self.app.test_client()
        replay.set_cookie(self.app.config["SESSION_COOKIE_NAME"], old_cookie)
        self.assertEqual(replay.get("/").status_code, 302)

    def test_admin_guard_uses_database_role_not_cookie_role(self):
        self._login()
        with self.client.session_transaction() as session:
            session["role"] = "system_administrator"
        self.assertEqual(self.client.get("/synthetic-admin-check").status_code, 403)
        admin_client = self.app.test_client()
        self._login("synthetic_admin", admin_client)
        self.assertEqual(admin_client.get("/synthetic-admin-check").status_code, 200)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE staff_users SET role = 'lgu_staff'")
        self.assertEqual(admin_client.get("/synthetic-admin-check").status_code, 403)

    def test_session_is_replaced_and_cookie_has_security_flags(self):
        token = self._token()
        with self.client.session_transaction() as session:
            session["untrusted_prelogin"] = "discard"
        response = self.client.post("/login", data={
            "username": "synthetic_staff", "password": self.password, "csrf_token": token,
        })
        cookie = response.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        with self.client.session_transaction() as session:
            self.assertNotIn("untrusted_prelogin", session)
            self.assertNotIn("password", session)
            self.assertNotIn("password_hash", session)
            self.assertNotIn("username", session)
            self.assertNotEqual(session["csrf_token"], token)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_expired_tampered_and_deactivated_sessions_are_denied(self):
        self._login()
        with self.client.session_transaction() as session:
            session["issued_at"] = int(time()) - 8 * 3600 - 1
        self.assertEqual(self.client.get("/").status_code, 302)
        self._login()
        cookie = self.client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        self.client.set_cookie(self.app.config["SESSION_COOKIE_NAME"], cookie + "tampered")
        self.assertEqual(self.client.get("/").status_code, 302)
        self._login()
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE staff_users SET is_active = false")
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_database_failure_does_not_leak_credentials_or_grant_access(self):
        token = self._token()
        with patch("services.auth.get_connection", side_effect=psycopg.OperationalError(
            "PRIVATE_DATABASE_DETAILS",
        )):
            response = self.client.post("/login", data={
                "username": "synthetic_staff", "password": self.password, "csrf_token": token,
            })
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE_DATABASE_DETAILS", response.text)
        self.assertNotIn(self.password, response.text)
        self.assertEqual(self.client.get("/").status_code, 302)
        self._login()
        with patch("services.auth.get_connection", side_effect=psycopg.OperationalError()):
            self.assertEqual(self.client.get("/").status_code, 503)

    def test_fixture_guard_remains_enforced_after_login(self):
        self._login()
        with patch.dict(self.app.extensions["development_workspace"], source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/").status_code, 403)

    def test_bootstrap_hashes_password_and_cannot_replace_accounts(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("DELETE FROM staff_users")
        with self.app.app_context():
            create_initial_admin("Synthetic_Bootstrap", self.password)
            with self.assertRaises(ValueError):
                create_initial_admin("second_admin", token_urlsafe(32))
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                "SELECT username, password_hash, role FROM staff_users",
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "synthetic_bootstrap")
        self.assertEqual(rows[0][2], "system_administrator")
        self.assertTrue(rows[0][1].startswith("scrypt:"))
        self.assertNotEqual(rows[0][1], self.password)
        self.assertTrue(check_password_hash(rows[0][1], self.password))

    def test_schema_rejects_plaintext_duplicate_accounts_and_invalid_roles(self):
        # Both numbered migrations coexist; authentication adds no survey columns.
        with psycopg.connect(self.url) as connection:
            connection.execute(
                (ROOT / "migrations/0001_database_foundation.sql").read_text(),
            )
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
                (self.schema,),
            ).fetchone()[0], 9)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM dataset_batches").fetchone()[0], 0,
            )
        for values in (("new_user", self.password, "lgu_staff"),
                       ("synthetic_staff", generate_password_hash(self.password), "lgu_staff"),
                       ("new_user", generate_password_hash(self.password), "unapproved_role")):
            with self.subTest(case=values[0]), psycopg.connect(self.url) as connection:
                with self.assertRaises(psycopg.IntegrityError):
                    with connection.transaction():
                        connection.execute(
                            """INSERT INTO staff_users (username, password_hash, role)
                               VALUES (%s, %s, %s)""", values,
                        )


if __name__ == "__main__":
    unittest.main()
