"""Citizen activation, sign-in, sessions and Change Password.

These tests use an isolated schema in the test database with synthetic data. No
email is sent and the development database is never touched.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import unittest
from datetime import timedelta
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash

from app import create_app
from repositories import citizen_accounts
from services import citizen_authentication as authentication
from services.citizen_credentials import new_activation_credentials
from services.public_programs import PUBLIC_PROGRAMS

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
SECRET = "s" * 48
CREDENTIAL_KEY = "k" * 48
COOKIE = "tanglaw_citizen_session"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "a different long password"
EMAIL = "citizen@example.com"


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic portal tests.")
class PortalTestCase(unittest.TestCase):
    def setUp(self):
        self.assertEqual(conninfo_to_dict(TEST_URL).get("dbname"), "tanglaw_buhay_test")
        self.schema = f"portal_check_{uuid4().hex}"
        self.db = psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True)
        self.addCleanup(self._drop_schema)
        self.db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.db.execute(path.read_text("utf-8"))
        self.db.execute(
            "INSERT INTO dataset_batches (survey_year, municipality, source_columns)"
            " VALUES (2022, 'Pozorrubio', %s)", (Jsonb({}),))
        self.batch = self.db.execute("SELECT id FROM dataset_batches").fetchone()[0]
        for number in (1, 2, 3):
            self.db.execute(
                "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                " VALUES (%s, %s, %s, %s)", (self.batch, f"H-{number}", number, Jsonb({})))
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": SECRET, "CITIZEN_CREDENTIAL_KEY": CREDENTIAL_KEY,
            "SESSION_COOKIE_SECURE": False,
            "DATABASE_URL": make_conninfo(
                TEST_URL, hostaddr="127.0.0.1", options=f"-c search_path={self.schema}"),
        })
        self.client = self.app.test_client()

    def _drop_schema(self):
        self.db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.db.close()

    # --- helpers -------------------------------------------------------------

    def make_pending(self, email=EMAIL, household="H-1"):
        """Create a pending account and return (account id, link token, code)."""
        account = citizen_accounts.insert_account(
            self.db, self.batch, household, "Synthetic Citizen", email)
        with self.app.app_context():
            credentials = new_activation_credentials()
        citizen_accounts.insert_token(
            self.db, account["id"], "activation", credentials.token_hash,
            credentials.code_hash, timedelta(hours=48))
        return account["id"], credentials.token, credentials.code

    def make_active(self, email=EMAIL, household="H-1", password=PASSWORD):
        account_id, token, code = self.make_pending(email, household)
        authentication_ok = self.activate_with_service(token, code, password)
        self.assertTrue(authentication_ok)
        return account_id

    def activate_with_service(self, token, code, password):
        with self.app.app_context():
            authentication.activate_account(token, code, password, password)
        return True

    def row(self, account_id):
        return self.db.execute(
            "SELECT status, failed_login_count, locked_until, session_version, password_hash"
            " FROM citizen_accounts WHERE id = %s", (account_id,)).fetchone()

    def token_row(self, token_hash=None):
        return self.db.execute(
            "SELECT failed_code_attempts, used_at IS NOT NULL, revoked_at IS NOT NULL"
            " FROM citizen_account_tokens ORDER BY id DESC LIMIT 1").fetchone()

    def csrf(self, client, path="/portal/login"):
        html = client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def sign_in(self, client=None, email=EMAIL, password=PASSWORD):
        client = client or self.client
        return client.post("/portal/login", data={
            "csrf_token": self.csrf(client), "email": email, "password": password})

    def post(self, path, data, client=None, form_page="/portal/login"):
        """POST with a real CSRF token taken from a page of the same browser session."""
        client = client or self.client
        return client.post(path, data={"csrf_token": self.csrf(client, form_page), **data})

    def text(self, response):
        return response.get_data(as_text=True)


class ActivationTests(PortalTestCase):
    def activate_path(self, token):
        return f"/portal/activate/{token}"

    def submit(self, token, code, password=NEW_PASSWORD, confirm=None):
        return self.post(self.activate_path(token), {
            "code": code, "new_password": password,
            "confirm_password": password if confirm is None else confirm})

    def test_valid_link_shows_the_form_without_revealing_the_code(self):
        _, token, code = self.make_pending()
        page = self.text(self.client.get(self.activate_path(token)))
        self.assertIn("Activate your account", page)
        self.assertNotIn(code, page)

    def test_unknown_or_malformed_links_get_the_same_friendly_page(self):
        for token in ("x" * 43, "short", "a" * 300):
            with self.subTest(token=token[:8]):
                response = self.client.get(self.activate_path(token))
                self.assertEqual(response.status_code, 410)
                self.assertIn("cannot be used", self.text(response))

    def test_correct_code_and_password_activate_the_account_then_go_to_sign_in(self):
        account_id, token, code = self.make_pending()
        response = self.submit(token, f"{code[:3]} {code[3:]}")
        self.assertEqual(response.status_code, 303)
        self.assertIn("/portal/login", response.location)
        status, _, _, version, stored_hash = self.row(account_id)
        self.assertEqual((status, version), ("active", 2))
        self.assertTrue(check_password_hash(stored_hash, NEW_PASSWORD))
        self.assertNotIn(NEW_PASSWORD, stored_hash)
        self.assertEqual(self.token_row(), (0, True, False))

    def test_a_used_link_cannot_be_used_again(self):
        _, token, code = self.make_pending()
        self.submit(token, code)
        again = self.submit(token, code)
        self.assertEqual(again.status_code, 410)

    def test_a_wrong_code_is_counted_and_saved_even_though_the_request_fails(self):
        _, token, code = self.make_pending()
        wrong = "000000" if code != "000000" else "111111"
        response = self.submit(token, wrong)
        self.assertEqual(response.status_code, 400)
        self.assertIn("4 tries left", self.text(response))
        self.assertEqual(self.token_row(), (1, False, False))

    def test_the_fifth_wrong_code_cancels_the_link_and_the_right_code_no_longer_works(self):
        account_id, token, code = self.make_pending()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.assertEqual(self.submit(token, wrong).status_code, 400)
        fifth = self.submit(token, wrong)
        self.assertEqual(fifth.status_code, 410)
        self.assertIn("cancelled", self.text(fifth))
        self.assertEqual(self.token_row(), (5, False, True))
        self.assertEqual(self.submit(token, code).status_code, 410)
        self.assertEqual(self.row(account_id)[0], "pending_activation")

    def test_typing_mistakes_in_the_form_do_not_use_up_attempts(self):
        _, token, code = self.make_pending()
        for response in (self.submit(token, "12"),                       # not a 6-digit code
                         self.submit(token, code, password="short"),     # password too short
                         self.submit(token, code, confirm="different")):  # passwords differ
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.token_row(), (0, False, False))
        self.assertEqual(self.submit(token, code).status_code, 303)

    def test_password_cannot_be_the_email_address(self):
        _, token, code = self.make_pending(email="long.email.address@example.com")
        response = self.submit(token, code, password="long.email.address@example.com")
        self.assertIn("cannot be your email address", self.text(response))

    def test_an_expired_link_is_refused(self):
        _, token, code = self.make_pending()
        self.db.execute("UPDATE citizen_account_tokens SET created_at = now() - interval '3 days',"
                        " expires_at = now() - interval '1 day'")
        self.assertEqual(self.client.get(self.activate_path(token)).status_code, 410)
        self.assertEqual(self.submit(token, code).status_code, 410)

    def test_a_replaced_link_stops_working(self):
        account_id, old_token, old_code = self.make_pending()
        citizen_accounts.revoke_open_tokens(self.db, account_id, "activation")
        self.assertEqual(self.client.get(self.activate_path(old_token)).status_code, 410)

    def test_the_form_needs_a_valid_csrf_token(self):
        _, token, code = self.make_pending()
        response = self.client.post(self.activate_path(token), data={
            "csrf_token": "forged", "code": code,
            "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.token_row(), (0, False, False))

    def test_simultaneous_wrong_codes_can_never_exceed_the_limit(self):
        _, token, code = self.make_pending()
        wrong = "000000" if code != "000000" else "111111"

        def attempt(_):
            with self.app.app_context():
                try:
                    authentication.activate_account(token, wrong, NEW_PASSWORD, NEW_PASSWORD)
                except authentication.ActivationLinkError as problem:
                    return problem.reason
                except authentication.CitizenFormError:
                    return "wrong_code"

        with ThreadPoolExecutor(max_workers=4) as pool:  # few workers: scrypt uses much memory
            outcomes = list(pool.map(attempt, range(10)))
        self.assertEqual(outcomes.count("wrong_code"), 4)
        self.assertEqual(outcomes.count("cancelled"), 1)
        self.assertEqual(outcomes.count("invalid"), 5)
        self.assertEqual(self.token_row(), (5, False, True))

    def test_missing_credential_key_closes_activation_but_nothing_is_activated(self):
        _, token, code = self.make_pending()
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = None
        self.assertEqual(self.client.get(self.activate_path(token)).status_code, 503)
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = CREDENTIAL_KEY
        self.assertEqual(self.token_row(), (0, False, False))


class SignInTests(PortalTestCase):
    def test_correct_email_and_password_sign_in_and_show_only_the_account_holder(self):
        self.make_active()
        response = self.sign_in(email="  Citizen@Example.COM ")
        self.assertEqual(response.status_code, 303)
        home = self.text(self.client.get("/portal/"))
        self.assertIn("Welcome, Synthetic Citizen", home)
        self.assertIn(EMAIL, home)
        for private in ("H-1", "responses", "uuid", "Pozorrubio"):
            self.assertNotIn(private, home)

    def test_every_failure_shows_the_same_message(self):
        self.make_active()
        self.make_pending(email="pending@example.com", household="H-2")
        for email, password in ((EMAIL, "wrong password!!"), ("nobody@example.com", PASSWORD),
                                ("pending@example.com", PASSWORD), ("", ""), ("x" * 300, "y")):
            with self.subTest(email=email[:12]):
                response = self.sign_in(email=email, password=password)
                self.assertEqual(response.status_code, 401)
                self.assertIn("We could not sign you in", self.text(response))

    def test_five_wrong_passwords_lock_the_account_even_for_the_right_password(self):
        account_id = self.make_active()
        for _ in range(5):
            self.sign_in(password="wrong password!!")
        self.assertIsNotNone(self.row(account_id)[2])
        refused = self.sign_in()
        self.assertEqual(refused.status_code, 401)
        self.assertEqual(self.row(account_id)[1], 5)  # a locked account is not counted further

    def test_the_lock_lasts_fifteen_minutes_and_then_counting_starts_again(self):
        account_id = self.make_active()
        for _ in range(5):
            self.sign_in(password="wrong password!!")
        minutes = self.db.execute(
            "SELECT round(extract(epoch FROM locked_until - now()) / 60) FROM citizen_accounts"
        ).fetchone()[0]
        self.assertEqual(minutes, 15)
        self.db.execute("UPDATE citizen_accounts SET locked_until = now() - interval '1 second'")
        self.assertEqual(self.sign_in(password="wrong password!!").status_code, 401)
        self.assertEqual(self.row(account_id)[1:3], (1, None))  # one failure, not locked again
        self.assertEqual(self.sign_in().status_code, 303)
        self.assertEqual(self.row(account_id)[1], 0)

    def test_deactivated_and_pending_accounts_cannot_sign_in(self):
        account_id = self.make_active()
        citizen_accounts.deactivate(self.db, account_id)
        self.assertEqual(self.sign_in().status_code, 401)

    def test_the_sign_in_form_needs_a_valid_csrf_token(self):
        self.make_active()
        response = self.client.post("/portal/login", data={
            "csrf_token": "forged", "email": EMAIL, "password": PASSWORD})
        self.assertEqual(response.status_code, 400)


class SessionTests(PortalTestCase):
    def cookie_headers(self, response):
        return [value for value in response.headers.getlist("Set-Cookie") if COOKIE in value]

    def test_cookie_is_http_only_lax_and_limited_to_the_portal(self):
        self.make_active()
        header = self.cookie_headers(self.sign_in())[0]
        for part in ("HttpOnly", "SameSite=Lax", "Path=/portal", "Max-Age=7200"):
            self.assertIn(part, header)
        self.assertNotIn("Secure", header)

    def test_cookie_is_secure_unless_local_http_is_explicitly_allowed(self):
        self.make_active()
        self.app.config["SESSION_COOKIE_SECURE"] = True
        client = self.app.test_client()
        header = self.cookie_headers(client.get("/portal/login"))[0]
        self.assertIn("Secure", header)

    def test_browsing_never_extends_the_two_hour_session(self):
        self.make_active()
        self.sign_in()
        for path in ("/portal/", "/portal/change-password", "/portal/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.cookie_headers(response), [])

    def test_an_expired_cookie_is_refused(self):
        self.make_active()
        self.sign_in()
        self.app.config["CITIZEN_SESSION_LIFETIME"] = timedelta(seconds=-1)
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/portal/login", response.location)

    def test_tampered_or_foreign_cookies_are_refused(self):
        self.make_active()
        self.sign_in()
        good = self.client.get_cookie(COOKIE, path="/portal").value
        staff_style = self.app.session_interface.get_signing_serializer(self.app).dumps(
            {"csrf": "x", "account_id": 1, "session_version": 2})
        for value in (good[:-3] + "abc", "garbage", staff_style):
            with self.subTest(value=value[:10]):
                client = self.app.test_client()
                client.set_cookie(COOKIE, value, path="/portal")
                self.assertEqual(client.get("/portal/").status_code, 302)

    def test_a_signed_in_citizen_cannot_reach_staff_pages(self):
        self.make_active()
        self.sign_in()
        for path in ("/assessments/prioritization", "/households", "/citizen-accounts/household/6f0a1c2e-1111-4222-8333-444455556666/new"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.location.endswith("/login"))
                self.assertNotIn("/portal", response.location)

    def test_a_staff_session_does_not_sign_a_citizen_in(self):
        self.make_active()
        with self.client.session_transaction() as staff_session:
            staff_session.update(user_id=1, session_version=1, issued_at=9999999999)
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/portal/login", response.location)

    def test_deactivating_the_account_ends_its_session_immediately(self):
        account_id = self.make_active()
        self.sign_in()
        self.assertEqual(self.client.get("/portal/").status_code, 200)
        citizen_accounts.deactivate(self.db, account_id)
        self.assertEqual(self.client.get("/portal/").status_code, 302)

    def test_sign_out_needs_csrf_and_ends_every_copy_of_the_session(self):
        self.make_active()
        self.sign_in()
        stolen_copy = self.client.get_cookie(COOKIE, path="/portal").value
        refused = self.client.post("/portal/logout", data={"csrf_token": "forged"})
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.client.get("/portal/").status_code, 200)
        self.assertEqual(self.client.get("/portal/logout").status_code, 405)
        done = self.client.post(
            "/portal/logout", data={"csrf_token": self.csrf(self.client, "/portal/change-password")})
        self.assertEqual(done.status_code, 303)
        self.assertEqual(self.client.get("/portal/").status_code, 302)
        replay = self.app.test_client()
        replay.set_cookie(COOKIE, stolen_copy, path="/portal")
        self.assertEqual(replay.get("/portal/").status_code, 302)

    def test_the_portal_is_closed_when_the_signing_secret_is_missing_or_weak(self):
        for secret in (None, "short"):
            with self.subTest(secret=secret):
                self.app.secret_key = secret
                self.assertEqual(self.client.get("/portal/login").status_code, 503)

    def test_portal_pages_are_not_cached_and_send_no_referrer(self):
        response = self.client.get("/portal/login")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")


class ChangePasswordTests(PortalTestCase):
    def change(self, current=PASSWORD, new=NEW_PASSWORD, confirm=None, client=None):
        return self.post("/portal/change-password", {
            "current_password": current, "new_password": new,
            "confirm_password": new if confirm is None else confirm}, client,
            form_page="/portal/change-password")

    def test_requires_sign_in(self):
        self.assertEqual(self.client.get("/portal/change-password").status_code, 302)

    def test_changing_the_password_replaces_it_and_ends_other_sessions(self):
        self.make_active()
        other_device = self.app.test_client()
        self.sign_in(other_device)
        self.sign_in()
        response = self.change()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Your password has been changed", self.text(response))
        self.assertEqual(self.client.get("/portal/").status_code, 200)   # this device stays in
        self.assertEqual(other_device.get("/portal/").status_code, 302)  # other devices are out
        self.client.post("/portal/logout", data={"csrf_token": self.csrf(self.client, "/portal/")})
        fresh = self.app.test_client()
        self.assertEqual(self.sign_in(fresh, password=PASSWORD).status_code, 401)
        self.assertEqual(self.sign_in(fresh, password=NEW_PASSWORD).status_code, 303)

    def test_form_mistakes_are_explained_and_nothing_changes(self):
        account_id = self.make_active()
        self.sign_in()
        cases = {
            "at least 12 characters": self.change(new="short"),
            "do not match": self.change(confirm="another long password"),
            "different from your current": self.change(new=PASSWORD),
            "Enter your current password": self.change(current=""),
        }
        for message, response in cases.items():
            with self.subTest(message=message):
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, self.text(response))
        self.assertTrue(check_password_hash(self.row(account_id)[4], PASSWORD))

    def test_a_wrong_current_password_is_counted_and_the_fifth_locks_and_signs_out(self):
        account_id = self.make_active()
        self.sign_in()
        for attempt in range(4):
            response = self.change(current="not my password!")
            self.assertIn("current password is not correct", self.text(response))
        self.assertEqual(self.row(account_id)[1], 4)
        fifth = self.change(current="not my password!")
        self.assertEqual(fifth.status_code, 401)
        self.assertIn("locked for 15 minutes", self.text(fifth))
        self.assertIsNotNone(self.row(account_id)[2])
        self.assertEqual(self.client.get("/portal/").status_code, 302)
        self.assertEqual(self.sign_in().status_code, 401)  # locked, even with the right password

    def test_the_form_needs_a_valid_csrf_token(self):
        account_id = self.make_active()
        self.sign_in()
        response = self.client.post("/portal/change-password", data={
            "csrf_token": "forged", "current_password": PASSWORD,
            "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(check_password_hash(self.row(account_id)[4], PASSWORD))


class ConcurrencyTests(PortalTestCase):
    """Simultaneous attempts must not get more than five tries. Four workers keep
    memory use low, because every password check needs about 32 MB."""

    WORKERS = 4

    def run_in_parallel(self, work, attempts=12):
        def one(_):
            with self.app.app_context():
                try:
                    work()
                except (authentication.SignInError, authentication.CitizenFormError):
                    pass
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            list(pool.map(one, range(attempts)))

    def test_simultaneous_wrong_passwords_are_counted_exactly_and_lock_the_account(self):
        account_id = self.make_active()
        self.run_in_parallel(lambda: authentication.sign_in(EMAIL, "wrong password!!"))
        _, count, locked_until, *_ = self.row(account_id)
        self.assertEqual(count, 5)
        self.assertIsNotNone(locked_until)

    def test_simultaneous_attempts_cannot_extend_an_existing_lock(self):
        account_id = self.make_active()
        self.db.execute("UPDATE citizen_accounts SET failed_login_count = 5,"
                        " locked_until = now() + interval '10 minutes'")
        before = self.row(account_id)[2]
        self.run_in_parallel(lambda: authentication.sign_in(EMAIL, "wrong password!!"), 8)
        _, count, after, *_ = self.row(account_id)
        self.assertEqual((count, after), (5, before))

    def test_simultaneous_wrong_current_passwords_cannot_exceed_five_tries(self):
        account_id = self.make_active()
        with self.app.app_context():
            account = authentication.sign_in(EMAIL, PASSWORD)
        _, _, _, version_before, _ = self.row(account_id)
        self.run_in_parallel(lambda: authentication.change_password(
            account, "not my password!", NEW_PASSWORD, NEW_PASSWORD))
        _, count, locked_until, version_after, stored = self.row(account_id)
        self.assertEqual(count, 5)
        self.assertIsNotNone(locked_until)
        self.assertEqual(version_after, version_before + 1)   # signed out once, everywhere
        self.assertTrue(check_password_hash(stored, PASSWORD))  # password never changed


class UnavailableServiceTests(unittest.TestCase):
    """A portal without a configured database shows a clear message, not a server error."""

    def setUp(self):
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": SECRET, "CITIZEN_CREDENTIAL_KEY": CREDENTIAL_KEY,
            "SESSION_COOKIE_SECURE": False, "DATABASE_URL": None,
        })
        self.client = self.app.test_client()

    def test_a_signed_in_cookie_with_no_database_gets_a_503_message(self):
        cookie = URLSafeTimedSerializer(SECRET, salt="tanglaw-citizen-session").dumps(
            {"csrf": "x", "account_id": 1, "session_version": 1})
        self.client.set_cookie(COOKIE, cookie, path="/portal")
        response = self.client.get("/portal/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.get_data(as_text=True))

    def test_signing_in_with_no_database_gets_a_503_message(self):
        page = self.client.get("/portal/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = self.client.post("/portal/login", data={
            "csrf_token": token, "email": EMAIL, "password": PASSWORD})
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.get_data(as_text=True))


class PublicPortalNavigationTests(PortalTestCase):
    def test_public_pages_link_to_the_portal(self):
        for path in ("/public", "/public/programs/4ps"):
            with self.subTest(path=path):
                self.assertIn('href="/portal/"', self.text(self.client.get(path)))

    def test_visitors_who_are_not_signed_in_reach_sign_in_and_citizens_reach_home(self):
        response = self.client.get("/portal/")
        self.assertIn("/portal/login", response.location)
        self.make_active()
        self.sign_in()
        self.assertEqual(self.client.get("/portal/").status_code, 200)

    def test_home_links_to_each_existing_public_program_page_and_nothing_private(self):
        self.make_active()
        self.sign_in()
        home = self.text(self.client.get("/portal/"))
        self.assertIn("Explore Subsidy Programs", home)
        for slug, program in PUBLIC_PROGRAMS.items():
            self.assertIn(f'href="/public/programs/{slug}"', home)
            self.assertIn(program["name"], home)
            self.assertEqual(self.client.get(f"/public/programs/{slug}").status_code, 200)
        for private in ("eligib", "approved", "assessment", "priority", "score", "h-1"):
            self.assertNotIn(private, home.lower())


if __name__ == "__main__":
    unittest.main()
