"""Minimal HTTP interface checks using generated accounts and mocked account SQL."""

import re
from secrets import token_urlsafe
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from app import create_app
from services.auth import admin_required


class MinimalStaffInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.password = token_urlsafe(32)
        password_hash = generate_password_hash(self.password)
        self.users = {
            "synthetic_admin": {"id": 1, "role": "system_administrator"},
            "synthetic_staff": {"id": 2, "role": "lgu_staff"},
        }
        for username, user in self.users.items():
            user.update(username=username, password_hash=password_hash,
                        is_active=True, session_version=1)
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None,
        })
        self.client = self.app.test_client()
        patches = (
            patch("psycopg.connect", side_effect=AssertionError("No database operations")),
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_username", side_effect=self._by_username),
            patch("repositories.staff_users.find_by_id", side_effect=self._by_id),
            patch("repositories.staff_users.invalidate_sessions", side_effect=self._revoke),
        )
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _by_username(self, connection, username):
        user = self.users.get(username)
        return dict(user) if user else None

    def _by_id(self, connection, user_id):
        user = next((user for user in self.users.values() if user["id"] == user_id), None)
        if user is None:
            return None
        return {key: value for key, value in user.items() if key != "password_hash"}

    def _revoke(self, connection, user_id):
        for user in self.users.values():
            if user["id"] == user_id:
                user["session_version"] += 1

    def _token(self, path="/login", client=None):
        page = (client or self.client).get(path)
        self.assertEqual(page.status_code, 200)
        return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)

    def _login(self, username="synthetic_staff", client=None):
        client = client or self.client
        return client.post("/login", data={
            "username": username, "password": self.password,
            "csrf_token": self._token(client=client),
        })

    def test_startup_and_health_never_load_records_or_run_engines(self):
        with patch("services.development_data.development_sources") as sources:
            with patch("services.assessments.build_unified_assessments") as assess:
                app = create_app({"TESTING": True, "SECRET_KEY": None, "DATABASE_URL": None})
        sources.assert_not_called()
        assess.assert_not_called()
        self.assertEqual(app.extensions["development_workspace"],
                         {"source_kind": "development_fixture"})
        self.assertEqual(app.test_client().get("/test").get_json(),
                         {"status": "ok", "application": "Tanglaw-Buhay"})

    def test_anonymous_staff_routes_redirect_and_login_has_no_staff_navigation(self):
        for path in ("/", "/administration", "/administration/import",
                     "/administration/accounts", "/logout", "/households", "/households/PRIVATE_ID",
                     "/assessments", "/assessments/PRIVATE_ID?tab=mpi"):
            page = self.client.get(path)
            self.assertEqual(page.status_code, 302)
            self.assertEqual(page.location, "/login")
            self.assertNotIn("PRIVATE_ID", page.text)
        page = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("sidebar", page.text)
        self.assertIn('name="csrf_token"', page.text)
        self.assertIn('href="/static/css/login.css"', page.text)

    def test_role_landing_pages_have_sign_out_and_no_record_content(self):
        for username, label in (("synthetic_admin", "System Administrator"),
                                ("synthetic_staff", "LGU Staff")):
            client = self.app.test_client()
            response = self._login(username, client)
            self.assertEqual(response.status_code, 302)
            destination = "/administration" if username == "synthetic_admin" else "/"
            self.assertEqual(response.location, destination)
            with patch("routes.main.get_active_dashboard_summary", return_value=None):
                page = client.get(destination)
            self.assertEqual(page.status_code, 200)
            self.assertIn("<h1>Dashboard</h1>", page.text)
            self.assertIn(label, page.text)
            self.assertIn("Sign Out", page.text)
            self.assertIn('action="/logout"', page.text)
            self.assertIn('method="post"', page.text)
            self.assertIn("Dashboard", page.text)
            if username == "synthetic_admin":
                self.assertIn('href="/administration"', page.text)
                self.assertIn('href="/administration/import"', page.text)
                self.assertIn('href="/administration/accounts"', page.text)
                self.assertIn("Data Import", page.text)
                self.assertIn("Staff Accounts", page.text)
                self.assertNotIn("Review Queue", page.text)
                self.assertNotIn("Submissions", page.text)
                self.assertEqual(client.get("/").location, "/administration")
            else:
                self.assertNotIn('href="/administration"', page.text)
                self.assertIn("Review Queue", page.text)
                self.assertIn("Submissions", page.text)
                self.assertNotIn("Data Import", page.text)
                self.assertNotIn("Staff Accounts", page.text)
            for retired in ("<table", "DEV-", "PRIVATE_RECORD_RESPONSE"):
                self.assertNotIn(retired, page.text)
            self.assertEqual(page.headers["Cache-Control"], "no-store")
            self.assertEqual(client.get("/login").location, destination)
            self.assertIn(f'href="{destination}"', client.get("/logout").text)

    def test_retired_routes_return_gone_for_both_roles_without_echoing_record_ids(self):
        for username in self.users:
            client = self.app.test_client()
            self._login(username, client)
            paths = ["/households/PRIVATE_ID", "/households/nested/PRIVATE_ID"]
            if username == "synthetic_staff":
                paths += ["/assessments?view=queue", "/assessments/PRIVATE_ID?tab=subsidy"]
            for path in paths:
                page = client.get(path)
                self.assertEqual(page.status_code, 410)
                self.assertTrue("has been retired" in page.text or "not available" in page.text)
                self.assertNotIn("PRIVATE_ID", page.text)
                self.assertNotIn("<table", page.text)
        for path in ("/static/css/app.css", "/static/js/navigation.js",
                     "/static/js/assessment.js", "/static/favicon.svg"):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_fixture_boundary_blocks_landing_without_loading_or_rendering_source_values(self):
        self._login()
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms", records="PRIVATE_RECORD_RESPONSE"):
            page = self.client.get("/")
            self.assertEqual(page.status_code, 403)
            self.assertNotIn("PRIVATE_RECORD_RESPONSE", page.text)
            self.assertEqual(self.client.get("/test").status_code, 200)
            self.assertEqual(self.client.get("/households").status_code, 503)

    def test_login_csrf_and_invalid_credentials_remain_protected(self):
        self.assertEqual(self.client.post("/login", data={
            "username": "synthetic_admin", "password": self.password,
        }).status_code, 400)
        page = self.client.post("/login", data={
            "username": "synthetic_admin", "password": token_urlsafe(32),
            "csrf_token": self._token(),
        })
        self.assertEqual(page.status_code, 401)
        self.assertIn("Invalid username or password.", page.text)
        self.assertEqual(self.client.get("/").location, "/login")

    def test_sign_out_requires_csrf_and_revokes_sessions(self):
        self._login()
        old_cookie = self.client.get_cookie(self.app.config["SESSION_COOKIE_NAME"]).value
        self.assertEqual(self.client.post("/logout").status_code, 400)
        response = self.client.post("/logout", data={"csrf_token": self._token("/")})
        self.assertEqual(response.location, "/login")
        self.assertEqual(self.client.get("/").location, "/login")
        replay = self.app.test_client()
        replay.set_cookie(self.app.config["SESSION_COOKIE_NAME"], old_cookie)
        self.assertEqual(replay.get("/").location, "/login")

    def test_reusable_admin_guard_rechecks_account_role(self):
        @self.app.get("/synthetic-admin-check")
        @admin_required
        def admin_check():
            return "Authorized"

        self._login()
        self.assertEqual(self.client.get("/synthetic-admin-check").status_code, 403)
        self.assertEqual(self.client.get("/administration").status_code, 403)
        admin_client = self.app.test_client()
        self._login("synthetic_admin", admin_client)
        self.assertEqual(admin_client.get("/synthetic-admin-check").status_code, 200)
        self.assertEqual(admin_client.get("/administration").status_code, 200)
        self.assertEqual(admin_client.get("/administration/import").status_code, 200)
        self.assertEqual(admin_client.get("/administration/accounts").status_code, 200)
        self.users["synthetic_admin"]["role"] = "lgu_staff"
        self.assertEqual(admin_client.get("/synthetic-admin-check").status_code, 403)
        self.assertEqual(admin_client.get("/administration").status_code, 403)
        self.assertEqual(admin_client.get("/administration/import").status_code, 403)
        self.assertEqual(admin_client.get("/administration/accounts").status_code, 403)

    def test_admin_cannot_open_assessments_or_submit_review_actions(self):
        self._login("synthetic_admin")
        for path in ("/assessments", "/assessments/queue", "/assessments/PRIVATE_ID",
                     "/assessments/00000000-0000-0000-0000-000000000001/reviews/4Ps"):
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post(
            "/assessments/00000000-0000-0000-0000-000000000001/reviews/4Ps",
            data={"csrf_token": "unused"},
        ).status_code, 403)


if __name__ == "__main__":
    unittest.main()
