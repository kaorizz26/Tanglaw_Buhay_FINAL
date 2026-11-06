"""Daily and per-account limits on citizen emails.

Database tests use an isolated synthetic schema and a mocked mail sender: no email is
sent and the rehearsal and development databases are never touched.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import time
import unittest
from unittest.mock import patch

from psycopg.types.json import Jsonb

from repositories import citizen_accounts as repository
from routes import citizen_accounts as routes
from services import citizen_authentication as authentication
from services import citizen_password_reset as reset_service
from services import email_limits
from services.citizen_accounts import CitizenAccountError
from services.citizen_credentials import new_activation_credentials

import test_citizen_account_creation as creation
import test_citizen_account_management as management
from test_citizen_account_routes import ASSESSMENT, CREATE, RESEND, account_row, household_page

PASSWORD = "a long synthetic password"


class LimitFixture(unittest.TestCase):
    """Borrow the isolated-schema fixture of the account creation tests."""

    def setUp(self):
        self.fx = creation.AccountCreationTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def token_count(self):
        return self.fx.connection.execute("SELECT count(*) FROM citizen_account_tokens").fetchone()[0]


@unittest.skipUnless(creation.TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class ActivationLimitTests(LimitFixture):
    def test_an_account_can_start_five_activation_emails_a_day_and_no_more(self):
        self.fx.create()
        for _ in range(4):
            self.fx.resend()
        self.assertEqual((self.fx.counts(), len(self.fx.sent)), ((1, 5, 5), 5))
        code = self.fx.error_code(self.fx.resend)
        self.assertEqual(code, "account_email_limit")
        # Nothing changed: no new link, no new history entry, no extra email, and the
        # latest link still works because the refused request was rolled back.
        self.assertEqual((self.fx.counts(), len(self.fx.sent)), ((1, 5, 5), 5))
        open_links = self.fx.connection.execute(
            "SELECT count(*) FROM citizen_account_tokens WHERE revoked_at IS NULL AND used_at IS NULL"
        ).fetchone()[0]
        self.assertEqual(open_links, 1)

    def test_cancelled_links_still_count_so_resending_cannot_reset_the_limit(self):
        self.fx.create()
        for _ in range(4):
            self.fx.resend()          # each resend cancels the one before it
        revoked = self.fx.connection.execute(
            "SELECT count(*) FROM citizen_account_tokens WHERE revoked_at IS NOT NULL").fetchone()[0]
        self.assertEqual(revoked, 4)
        self.assertEqual(self.fx.error_code(self.fx.resend), "account_email_limit")

    def test_the_daily_limit_covers_creation_and_resends(self):
        with patch.object(email_limits, "DAILY_EMAIL_LIMIT", 2):
            self.fx.create()
            self.fx.resend()
            self.assertEqual(self.fx.error_code(self.fx.resend), "daily_email_limit")
        self.assertEqual((self.fx.counts(), len(self.fx.sent)), ((1, 2, 2), 2))

    def test_creation_over_the_daily_limit_saves_nothing_and_sends_nothing(self):
        with patch.object(email_limits, "DAILY_EMAIL_LIMIT", 0):
            self.assertEqual(self.fx.error_code(self.fx.create), "daily_email_limit")
        self.assertEqual((self.fx.counts(), self.fx.sent), ((0, 0, 0), []))

    def test_a_failed_email_still_counts_because_it_counts_attempts_not_deliveries(self):
        self.fx.send.side_effect = creation.service.EmailDeliveryError("send_failed")
        self.fx.create()
        self.assertEqual(self.token_count(), 1)
        with patch.object(email_limits, "DAILY_EMAIL_LIMIT", 1):
            self.assertEqual(self.fx.error_code(self.fx.resend), "daily_email_limit")


@unittest.skipUnless(creation.TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class ResetLimitTests(LimitFixture):
    def setUp(self):
        super().setUp()
        result = self.fx.create()
        authentication.activate_account(
            self.fx.sent[0]["token"], result["activation_code"], PASSWORD, PASSWORD,
            connection_factory=self.fx._connect)
        self.resets = []
        patcher = patch.object(reset_service, "send_password_reset_email",
                               side_effect=lambda address, token: self.resets.append(address))
        patcher.start()
        self.addCleanup(patcher.stop)

    def request_reset(self, email="citizen@example.com"):
        return reset_service.request_password_reset(email, connection_factory=self.fx._connect)

    def test_resets_count_toward_the_daily_limit_without_telling_the_visitor(self):
        with patch.object(email_limits, "DAILY_EMAIL_LIMIT", self.token_count() + 1):
            answers = [self.request_reset(), self.request_reset(), self.request_reset("nobody@example.com")]
        self.assertEqual(answers, [None, None, None])       # the visitor sees the same result
        self.assertEqual(self.resets, ["citizen@example.com"])  # but only the first sent mail
        self.assertEqual(self.token_count(), 2)                # and only it created a link

    def test_a_reset_is_sent_normally_while_the_day_has_room(self):
        self.assertIsNone(self.request_reset())
        self.assertEqual(self.resets, ["citizen@example.com"])


@unittest.skipUnless(creation.TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class ConcurrencyTests(LimitFixture):
    def test_simultaneous_requests_can_never_exceed_the_daily_limit(self):
        """Eight accounts ask at once for three remaining slots; exactly three succeed."""
        execute = self.fx.connection.execute
        account_ids = []
        for number in range(8):
            household = f"00000000-0000-4000-8000-0000000001{number:02d}"
            execute("INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                    " VALUES (%s, %s, %s, %s)", (self.fx.batch, household, 100 + number, Jsonb({})))
            account_ids.append(repository.insert_account(
                self.fx.connection, self.fx.batch, household, "Synthetic Citizen",
                f"citizen{number}@example.com")["id"])

        def ask(account_id):
            with self.fx.app.app_context():
                credentials = new_activation_credentials()
                with self.fx._connect() as connection:
                    with connection.transaction():
                        if not email_limits.daily_budget_left(connection):
                            return False
                        time.sleep(0.2)  # a slow request: without the lock, others would pass the check now
                        repository.insert_token(
                            connection, account_id, "activation", credentials.token_hash,
                            credentials.code_hash, timedelta(hours=1))
                        return True

        with patch.object(email_limits, "DAILY_EMAIL_LIMIT", 3):
            with ThreadPoolExecutor(max_workers=4) as pool:  # few workers: scrypt uses much memory
                outcomes = list(pool.map(ask, account_ids))
        self.assertEqual((outcomes.count(True), self.token_count()), (3, 3))


class LimitMessageRouteTests(management.ManagementRouteCase):
    """Staff see a plain message on the Assessment page, and nothing was saved."""

    def flashed(self):
        with self.client.session_transaction() as session:
            return dict(session.get("_flashes", []))

    def assert_limit_message(self, response, code):
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.assertEqual(self.flashed(), {"citizen_account": routes.EMAIL_LIMIT_MESSAGES[code]})

    def test_create_and_resend_return_to_the_assessment_with_the_message(self):
        for code in routes.EMAIL_LIMIT_MESSAGES:
            with self.subTest(code=code):
                self.page = household_page()
                self.mocks["create"].side_effect = CitizenAccountError(code)
                self.sign_in()
                self.assert_limit_message(self.submit(CREATE), code)
                self.page = household_page(account_row())
                self.mocks["resend"].side_effect = CitizenAccountError(code)
                self.assert_limit_message(self.submit(RESEND), code)

    def test_reactivation_and_email_change_return_with_the_message(self):
        for code in routes.EMAIL_LIMIT_MESSAGES:
            for path, status, name in ((management.REACTIVATE, "deactivated", "reactivate_account"),
                                       (management.CHANGE_EMAIL, "active", "change_account_email")):
                with self.subTest(code=code, name=name):
                    self.mocks[name].side_effect = CitizenAccountError(code)
                    self.page = household_page(account_row(status))
                    response = self.client.post(path, data=self.form_for(path))
                    self.assert_limit_message(response, code)

    def test_the_messages_are_plain_and_name_no_technical_detail(self):
        for message in routes.EMAIL_LIMIT_MESSAGES.values():
            self.assertIn("try again tomorrow", message.lower())
            for word in ("token", "database", "smtp", "gmail", "limit exceeded"):
                self.assertNotIn(word, message.lower())


if __name__ == "__main__":
    unittest.main()
