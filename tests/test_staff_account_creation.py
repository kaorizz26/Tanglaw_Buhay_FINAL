"""Administrator account creation in an isolated synthetic test schema."""

import os
from pathlib import Path
import re
from secrets import token_urlsafe
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv
from cryptography.fernet import Fernet
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
                                  "0010_staff_password_change_required.sql",
                                  "0011_staff_temporary_password.sql",
                                  "0012_staff_account_deletion.sql"):
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
            "STAFF_TEMP_PASSWORD_KEY": Fernet.generate_key().decode("ascii"),
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

    def test_admin_creates_staff_and_can_reveal_encrypted_initial_password(self):
        self._login()
        token = self._csrf(self.client, "/administration/accounts")
        page = self.client.get("/administration/accounts/create")
        self.assertIn("Create staff account", page.text)
        self.assertIn('src="/static/js/staff-accounts.js"', page.text)
        response = self.client.post("/administration/accounts/create", data={
            "csrf_token": token, "display_name": "  Ana   Dela Cruz  ",
            "username": "Ana.Dela.Cruz", "role": "system_administrator",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("Account created", response.text)
        self.assertIn("Ana Dela Cruz", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        initial_password = re.search(r'<code id="created-temp-password">([^<]+)</code>', response.text).group(1)
        self.assertRegex(initial_password, r"^[A-HJ-NP-Z2-9]{16}$")
        with psycopg.connect(self.url) as connection:
            account = connection.execute(
                """SELECT display_name, username, password_hash, role, is_active,
                          must_change_password, temporary_password_encrypted, id FROM staff_users
                   WHERE username = 'ana.dela.cruz'""",
            ).fetchone()
        self.assertEqual(account[:2], ("Ana Dela Cruz", "ana.dela.cruz"))
        self.assertNotEqual(account[2], initial_password)
        self.assertTrue(check_password_hash(account[2], initial_password))
        self.assertEqual(account[3:6], ("lgu_staff", True, True))
        self.assertNotIn(initial_password, account[6])
        self.assertEqual(Fernet(self.app.config["STAFF_TEMP_PASSWORD_KEY"]).decrypt(
            account[6],
        ).decode(), initial_password)
        self.assertNotIn(initial_password, self.client.get("/administration/accounts").text)
        for _ in range(2):
            revealed = self.client.post("/administration/accounts", data={
                "csrf_token": token, "action": "reveal", "account_id": account[7],
            })
            self.assertEqual(revealed.status_code, 200)
            self.assertIn(initial_password, revealed.text)
            self.assertIn('id="temporary-password-dialog"', revealed.text)
            self.assertIn('method="dialog"', revealed.text)
            self.assertIn('src="/static/js/staff-accounts.js"', revealed.text)
            self.assertNotIn('class="accounts-card accounts-created"', revealed.text)
            self.assertEqual(revealed.headers["Cache-Control"], "no-store")
            self.assertNotIn(account[6], revealed.text)
        with self.client.session_transaction() as session:
            self.assertNotIn(initial_password, str(dict(session)))
            self.assertNotIn(account[6], str(dict(session)))
        with self.app.app_context():
            from services.auth import authenticate
            authenticated = authenticate("ana.dela.cruz", initial_password)
            self.assertNotIn("temporary_password_encrypted", authenticated)

    def test_creation_rejects_duplicate_invalid_and_unauthorized_requests(self):
        self.assertEqual(self.client.post("/administration/accounts").location, "/login")
        self._login()
        self.assertEqual(self.client.post("/administration/accounts/create", data={
            "display_name": "Ana Dela Cruz", "username": "ana.dela.cruz",
        }).status_code, 400)
        token = self._csrf(self.client, "/administration/accounts")
        for display_name, username in (("", "ana.dela.cruz"),
                                       ("Ana Dela Cruz", "bad username"),
                                       ("Ana Dela Cruz", "a")):
            with self.subTest(username=username):
                response = self.client.post("/administration/accounts/create", data={
                    "csrf_token": token, "display_name": display_name,
                    "username": username,
                })
                self.assertEqual(response.status_code, 400)
        duplicate = self.client.post("/administration/accounts/create", data={
            "csrf_token": token, "display_name": "Another Person",
            "username": "Synthetic_Admin",
        })
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already in use", duplicate.text)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            blocked = self.client.post("/administration/accounts/create", data={
                "csrf_token": token, "display_name": "Another Person",
                "username": "another.person",
            })
        self.assertEqual(blocked.status_code, 403)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM staff_users").fetchone()[0], 2)

        staff_client = self.app.test_client()
        self._login("synthetic_staff", staff_client)
        self.assertEqual(staff_client.get("/administration/accounts").status_code, 403)
        self.assertEqual(staff_client.post("/administration/accounts/create", data={
            "csrf_token": token, "display_name": "Another Person", "username": "another.person",
        }).status_code, 403)

    def test_new_staff_sign_in_requires_password_setup(self):
        self._login()
        created = self.client.post("/administration/accounts/create", data={
            "csrf_token": self._csrf(self.client, "/administration/accounts"),
            "display_name": "Ana Dela Cruz", "username": "ana.dela.cruz",
        })
        self.assertEqual(created.status_code, 200)
        initial_password = re.search(r'<code id="created-temp-password">([^<]+)</code>', created.text).group(1)
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
            password_hash, must_change, version, encrypted, account_id = connection.execute(
                """SELECT password_hash, must_change_password, session_version,
                          temporary_password_encrypted, id
                   FROM staff_users WHERE username = 'ana.dela.cruz'""",
            ).fetchone()
        self.assertFalse(must_change)
        self.assertIsNone(encrypted)
        self.assertEqual(version, 2)
        self.assertTrue(check_password_hash(password_hash, new_password))
        self.assertFalse(check_password_hash(password_hash, initial_password))
        stale_reveal = self.client.post("/administration/accounts", data={
            "csrf_token": self._csrf(self.client, "/administration/accounts"),
            "action": "reveal", "account_id": account_id,
        })
        self.assertEqual(stale_reveal.status_code, 400)
        self.assertNotIn(initial_password, stale_reveal.text)
        self.assertNotIn(new_password, stale_reveal.text)
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

    def _create_pending_account(self):
        self._login()
        token = self._csrf(self.client, "/administration/accounts")
        response = self.client.post("/administration/accounts/create", data={
            "csrf_token": token, "display_name": "Pending Staff", "username": "pending.staff",
        })
        self.assertEqual(response.status_code, 200)
        password = re.search(r'<code id="created-temp-password">([^<]+)</code>', response.text).group(1)
        with psycopg.connect(self.url) as connection:
            account_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'pending.staff'",
            ).fetchone()[0]
        return account_id, token, password

    def test_directory_statuses_empty_state_and_no_secrets(self):
        account_id, _, password = self._create_pending_account()
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """INSERT INTO staff_users
                   (username, password_hash, role, is_active, must_change_password)
                   VALUES ('inactive.staff', %s, 'lgu_staff', false, true),
                          ('legacy.pending', %s, 'lgu_staff', true, true)""",
                (generate_password_hash(self.password), generate_password_hash(self.password)),
            )
        page = self.client.get("/administration/accounts")
        table = re.search(r"<tbody>(.*?)</tbody>", page.text, re.S).group(1)
        for value in ("Pending Staff", "pending.staff", "Pending password setup", "Ready",
                      "Inactive", "Name not provided", "created before password retrieval"):
            self.assertIn(value, table)
        self.assertNotIn("synthetic_admin", table)
        self.assertNotIn(password, page.text)
        self.assertNotIn("scrypt:", page.text)
        self.assertEqual(table.count('name="action" value="reveal"'), 1)
        with psycopg.connect(self.url) as connection:
            connection.execute("DELETE FROM staff_users WHERE role = 'lgu_staff'")
        self.assertIn("No staff accounts yet", self.client.get("/administration/accounts").text)

    def test_reveal_checks_csrf_role_activity_and_target(self):
        account_id, token, password = self._create_pending_account()
        form = {"action": "reveal", "account_id": account_id}
        self.assertEqual(self.client.post("/administration/accounts", data=form).status_code, 400)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post("/administration/accounts", data=form).location, "/login")
        staff = self.app.test_client()
        self._login("synthetic_staff", staff)
        form["csrf_token"] = self._csrf(staff, "/logout")
        self.assertEqual(staff.post("/administration/accounts", data=form).status_code, 403)
        form["csrf_token"] = token
        for target in (None, "bad", "-1", "9" * 40, 999999, 1, 2):
            with self.subTest(target=target):
                response = self.client.post("/administration/accounts", data={
                    **form, "account_id": target,
                })
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(password, response.text)
        with patch.dict(self.app.extensions["development_workspace"], source_kind="actual_cbms"):
            self.assertEqual(self.client.post("/administration/accounts", data=form).status_code, 403)
        with psycopg.connect(self.url) as connection:
            connection.execute("UPDATE staff_users SET is_active = false WHERE id = %s", (account_id,))
        response = self.client.post("/administration/accounts", data=form)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(password, response.text)
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """UPDATE staff_users SET is_active = true, temporary_password_encrypted = NULL
                   WHERE id = %s""", (account_id,),
            )
        self.assertEqual(self.client.post("/administration/accounts", data=form).status_code, 400)

    def test_encryption_errors_fail_without_exposing_or_creating_credentials(self):
        account_id, token, password = self._create_pending_account()
        form = {"action": "reveal", "account_id": account_id, "csrf_token": token}
        original_key = self.app.config["STAFF_TEMP_PASSWORD_KEY"]
        for key in (None, "invalid-key", Fernet.generate_key().decode()):
            with self.subTest(key_configured=bool(key)):
                self.app.config["STAFF_TEMP_PASSWORD_KEY"] = key
                response = self.client.post("/administration/accounts", data=form)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(password, response.text)
        self.app.config["STAFF_TEMP_PASSWORD_KEY"] = None
        self.assertEqual(self.client.post("/administration/accounts/create", data={
            "csrf_token": token, "display_name": "Uncreated Staff", "username": "uncreated.staff",
        }).status_code, 503)
        self.app.config["STAFF_TEMP_PASSWORD_KEY"] = original_key
        with psycopg.connect(self.url) as connection:
            self.assertIsNone(connection.execute(
                "SELECT id FROM staff_users WHERE username = 'uncreated.staff'",
            ).fetchone())
            connection.execute(
                "UPDATE staff_users SET temporary_password_encrypted = 'tampered' WHERE id = %s",
                (account_id,),
            )
        response = self.client.post("/administration/accounts", data=form)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("tampered", response.text)

    def test_admin_deletes_staff_account_and_preserves_attributed_identity(self):
        self._login()
        token = self._csrf(self.client, "/administration/accounts")
        with psycopg.connect(self.url) as connection:
            staff_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'synthetic_staff'",
            ).fetchone()[0]
            connection.execute(
                """CREATE TABLE attributed_record (
                       id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                       staff_id bigint NOT NULL REFERENCES staff_users (id))""",
            )
            connection.execute(
                "INSERT INTO attributed_record (staff_id) VALUES (%s)", (staff_id,),
            )

        staff_client = self.app.test_client()
        self._login("synthetic_staff", staff_client)
        url = f"/administration/accounts/{staff_id}/delete"
        list_page = self.client.get("/administration/accounts")
        self.assertIn(f'action="{url}"', list_page.text)
        self.assertIn('class="accounts-delete-button" type="submit"', list_page.text)
        confirmation = self.client.get(url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("synthetic_staff", confirmation.text)
        self.assertIn("cannot be used for a new account", confirmation.text)
        self.assertEqual(self.client.post(url).status_code, 400)
        self.assertEqual(self.client.get(url).status_code, 200)

        response = self.client.post(url, data={"csrf_token": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/administration/accounts")
        page = self.client.get(response.location)
        self.assertIn("Staff account deleted.", page.text)
        self.assertNotIn("synthetic_staff", page.text)
        self.assertEqual(staff_client.get("/").location, "/login")
        self.assertEqual(staff_client.post("/login", data={
            "csrf_token": self._csrf(staff_client, "/login"),
            "username": "synthetic_staff", "password": self.password,
        }).status_code, 401)
        with psycopg.connect(self.url) as connection:
            account = connection.execute(
                """SELECT is_active, deleted_at, session_version,
                          temporary_password_encrypted
                   FROM staff_users WHERE id = %s""", (staff_id,),
            ).fetchone()
            self.assertEqual(connection.execute(
                "SELECT staff_id FROM attributed_record",
            ).fetchone()[0], staff_id)
        self.assertFalse(account[0])
        self.assertIsNotNone(account[1])
        self.assertEqual(account[2], 2)
        self.assertIsNone(account[3])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, data={"csrf_token": token}).status_code, 404)

    def test_only_admin_can_delete_staff_and_admin_account_cannot_be_deleted(self):
        self._login()
        token = self._csrf(self.client, "/administration/accounts")
        with psycopg.connect(self.url) as connection:
            admin_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'synthetic_admin'",
            ).fetchone()[0]
            staff_id = connection.execute(
                "SELECT id FROM staff_users WHERE username = 'synthetic_staff'",
            ).fetchone()[0]
        admin_url = f"/administration/accounts/{admin_id}/delete"
        staff_url = f"/administration/accounts/{staff_id}/delete"
        self.assertEqual(self.client.get(admin_url).status_code, 404)
        self.assertEqual(self.client.post(admin_url, data={"csrf_token": token}).status_code, 404)
        self.assertEqual(self.app.test_client().post(staff_url).location, "/login")
        staff_client = self.app.test_client()
        self._login("synthetic_staff", staff_client)
        self.assertEqual(staff_client.get(staff_url).status_code, 403)
        self.assertEqual(staff_client.post(staff_url, data={
            "csrf_token": self._csrf(staff_client, "/logout"),
        }).status_code, 403)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            self.assertEqual(self.client.post(staff_url, data={
                "csrf_token": token,
            }).status_code, 403)
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM staff_users WHERE deleted_at IS NOT NULL",
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
