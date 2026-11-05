"""Citizen forgot-password and reset, in an isolated schema with mocked email delivery.

No email is sent: the two send functions are replaced by mocks, so the tests can
read the reset link token and check who would have been emailed.
"""

from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from werkzeug.security import check_password_hash

from services import citizen_password_reset as reset_service
from services.email_delivery import EmailDeliveryError

import test_citizen_portal as portal

FORGOT = "/portal/forgot-password"
EMAIL, PASSWORD, NEW_PASSWORD = portal.EMAIL, portal.PASSWORD, portal.NEW_PASSWORD


class ResetTestCase(portal.PortalTestCase):
    def setUp(self):
        super().setUp()
        reset_email = patch.object(reset_service, "send_password_reset_email")
        changed_email = patch.object(reset_service, "send_password_changed_email")
        self.reset_email = reset_email.start()
        self.changed_email = changed_email.start()
        self.addCleanup(reset_email.stop)
        self.addCleanup(changed_email.stop)

    def ask(self, email=EMAIL, client=None):
        """Submit the forgot-password form and return the response."""
        return self.post(FORGOT, {"email": email}, client, form_page=FORGOT)

    def token_from_email(self):
        """The link token that would have been emailed in the most recent request."""
        return self.reset_email.call_args.args[1]

    def submit(self, token, password=NEW_PASSWORD, confirm=None, client=None):
        return self.post(f"/portal/reset/{token}", {
            "new_password": password, "confirm_password": password if confirm is None else confirm,
        }, client, form_page="/portal/login")

    def reset_tokens(self):
        return self.db.execute(
            "SELECT count(*) FROM citizen_account_tokens WHERE purpose = 'password_reset'",
        ).fetchone()[0]


class RequestTests(ResetTestCase):
    def test_sign_in_page_links_to_forgot_password(self):
        page = self.text(self.client.get("/portal/login"))
        self.assertIn(f'href="{FORGOT}"', page)

    def test_an_active_account_is_emailed_a_link_and_sees_the_generic_page(self):
        self.make_active()
        response = self.ask()
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith(f"{FORGOT}?sent=1"))
        self.reset_email.assert_called_once()
        self.assertEqual(self.reset_email.call_args.args[0], EMAIL)
        page = self.text(self.client.get(f"{FORGOT}?sent=1"))
        self.assertIn("If this email address has a Citizen Account", page)
        self.assertNotIn(self.token_from_email(), page)

    def test_every_kind_of_address_gets_exactly_the_same_answer(self):
        self.make_active()
        pending_id, _, _ = self.make_pending(email="pending@example.com", household="H-2")
        deactivated = self.make_active(email="gone@example.com", household="H-3")
        self.db.execute("UPDATE citizen_accounts SET status = 'deactivated' WHERE id = %s",
                        (deactivated,))
        answers = set()
        for email in (EMAIL, "  CITIZEN@Example.COM ", "unknown@example.com",
                      "pending@example.com", "gone@example.com", "", "x" * 400, "not-an-email"):
            with self.subTest(email=email[:20]):
                response = self.ask(email)
                answers.add((response.status_code, response.location.split("?")[-1]))
        self.assertEqual(answers, {(303, "sent=1")})
        # Only the two requests for the active account sent an email.
        self.assertEqual(self.reset_email.call_count, 2)
        self.assertEqual({call.args[0] for call in self.reset_email.call_args_list}, {EMAIL})

    def test_an_email_failure_is_not_shown_and_the_request_still_counts(self):
        self.make_active()
        self.reset_email.side_effect = EmailDeliveryError("connection_failed")
        response = self.ask()
        self.assertEqual((response.status_code, response.location.split("?")[-1]), (303, "sent=1"))
        self.assertEqual(self.reset_tokens(), 1)

    def test_the_link_lasts_one_hour(self):
        self.make_active()
        self.ask()
        minutes = self.db.execute(
            "SELECT round(extract(epoch FROM expires_at - created_at) / 60)"
            " FROM citizen_account_tokens WHERE purpose = 'password_reset'").fetchone()[0]
        self.assertEqual(minutes, 60)

    def test_the_link_has_no_code_and_a_new_request_replaces_the_old_link(self):
        self.make_active()
        self.ask()
        first = self.token_from_email()
        self.ask()
        second = self.token_from_email()
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(f"/portal/reset/{first}").status_code, 410)
        self.assertEqual(self.client.get(f"/portal/reset/{second}").status_code, 200)
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM citizen_account_tokens WHERE purpose = 'password_reset'"
            " AND code_hash IS NOT NULL").fetchone()[0], 0)

    def test_only_three_requests_per_account_per_hour_and_nobody_else_is_affected(self):
        self.make_active()
        self.make_active(email="other@example.com", household="H-2")
        for _ in range(5):
            self.ask()
        self.assertEqual(self.reset_email.call_count, 3)
        self.assertEqual(self.reset_tokens(), 3)
        self.ask("other@example.com")
        self.assertEqual(self.reset_email.call_count, 4)
        # The limit is the same generic page, so the citizen cannot tell it applies.
        self.assertEqual(self.ask().location.split("?")[-1], "sent=1")

    def test_requests_older_than_an_hour_no_longer_count(self):
        account_id = self.make_active()
        for _ in range(3):
            self.ask()
        self.db.execute("UPDATE citizen_account_tokens SET created_at = now() - interval '61 minutes',"
                        " expires_at = now() - interval '1 minute' WHERE purpose = 'password_reset'")
        self.ask()
        self.assertEqual(self.reset_email.call_count, 4)

    def test_simultaneous_requests_cannot_exceed_three_links(self):
        self.make_active()

        def one(_):
            with self.app.app_context():
                reset_service.request_password_reset(EMAIL)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, range(10)))
        self.assertEqual(self.reset_tokens(), 3)
        self.assertEqual(self.reset_email.call_count, 3)

    def test_forms_need_a_valid_csrf_token(self):
        self.make_active()
        response = self.client.post(FORGOT, data={"csrf_token": "forged", "email": EMAIL})
        self.assertEqual(response.status_code, 400)
        self.reset_email.assert_not_called()

    def test_without_a_secret_key_every_address_gets_the_same_unavailable_page(self):
        self.make_active()
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = None
        for email in (EMAIL, "unknown@example.com"):
            self.assertEqual(self.ask(email).status_code, 503)

    def test_a_signed_in_citizen_is_sent_to_change_password_instead(self):
        self.make_active()
        self.sign_in()
        self.assertIn("/portal/change-password", self.client.get(FORGOT).location)


class ResetTests(ResetTestCase):
    def start_reset(self):
        account_id = self.make_active()
        self.ask()
        return account_id, self.token_from_email()

    def test_opening_the_link_does_not_use_it_up_and_never_prints_the_token(self):
        _, token = self.start_reset()
        for _ in range(3):
            page = self.client.get(f"/portal/reset/{token}")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(token, self.text(page))
        self.assertEqual(self.reset_tokens(), 1)
        self.assertEqual(self.submit(token).status_code, 303)

    def test_a_valid_reset_changes_the_password_and_sends_the_notice(self):
        account_id, token = self.start_reset()
        response = self.submit(token)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.location.endswith("/portal/login?reset=1"))
        self.changed_email.assert_called_once_with(EMAIL)
        self.assertTrue(check_password_hash(self.row(account_id)[4], NEW_PASSWORD))
        fresh = self.app.test_client()
        self.assertEqual(self.sign_in(fresh, password=PASSWORD).status_code, 401)
        self.assertEqual(self.sign_in(fresh, password=NEW_PASSWORD).status_code, 303)
        login = self.text(self.client.get("/portal/login?reset=1"))
        self.assertIn("Your password has been changed", login)

    def test_a_reset_signs_out_every_existing_session(self):
        _, token = self.start_reset()
        other_device = self.app.test_client()
        self.sign_in(other_device)
        self.assertEqual(other_device.get("/portal/").status_code, 200)
        self.submit(token, client=self.app.test_client())
        self.assertEqual(other_device.get("/portal/").status_code, 302)

    def test_a_link_works_only_once(self):
        _, token = self.start_reset()
        self.assertEqual(self.submit(token).status_code, 303)
        self.assertEqual(self.client.get(f"/portal/reset/{token}").status_code, 410)
        self.assertEqual(self.submit(token, password="yet another long one").status_code, 410)
        self.assertEqual(self.changed_email.call_count, 1)

    def test_unknown_expired_and_inactive_links_all_show_the_same_page(self):
        account_id, token = self.start_reset()
        self.assertIn("cannot be used", self.text(self.client.get("/portal/reset/" + "x" * 43)))
        self.db.execute("UPDATE citizen_account_tokens SET created_at = now() - interval '2 hours',"
                        " expires_at = now() - interval '1 hour'")
        expired = self.client.get(f"/portal/reset/{token}")
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(self.submit(token).status_code, 410)
        # A valid link for an account that was deactivated meanwhile also stops working.
        self.db.execute("UPDATE citizen_account_tokens SET created_at = now(),"
                        " expires_at = now() + interval '1 hour'")
        self.db.execute("UPDATE citizen_accounts SET status = 'deactivated'")
        self.assertEqual(self.client.get(f"/portal/reset/{token}").status_code, 410)
        self.assertEqual(self.submit(token).status_code, 410)

    def test_password_mistakes_are_explained_and_do_not_use_up_the_link(self):
        _, token = self.start_reset()
        for password, confirm, message in (
            ("short", None, "at least 12 characters"),
            (NEW_PASSWORD, "another long password", "do not match"),
            (EMAIL, None, "cannot be your email address"),
        ):
            with self.subTest(message=message):
                response = self.submit(token, password, confirm)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, self.text(response))
                self.assertNotIn(token, self.text(response))
        self.assertEqual(self.submit(token).status_code, 303)

    def test_a_failed_notice_email_does_not_undo_the_reset(self):
        account_id, token = self.start_reset()
        self.changed_email.side_effect = EmailDeliveryError("connection_failed")
        self.assertEqual(self.submit(token).status_code, 303)
        self.assertTrue(check_password_hash(self.row(account_id)[4], NEW_PASSWORD))

    def test_a_reset_unlocks_a_locked_account(self):
        account_id, token = self.start_reset()
        self.db.execute("UPDATE citizen_accounts SET failed_login_count = 5,"
                        " locked_until = now() + interval '10 minutes'")
        self.submit(token)
        self.assertEqual(self.row(account_id)[1:3], (0, None))

    def test_the_form_needs_a_valid_csrf_token_and_changes_nothing_without_it(self):
        account_id, token = self.start_reset()
        response = self.client.post(f"/portal/reset/{token}", data={
            "csrf_token": "forged", "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(check_password_hash(self.row(account_id)[4], PASSWORD))

    def test_simultaneous_submissions_of_one_link_succeed_only_once(self):
        _, token = self.start_reset()
        passwords = [f"password number {number} here" for number in range(4)]

        def one(password):
            with self.app.app_context():
                try:
                    reset_service.reset_password(token, password, password)
                    return "changed"
                except reset_service.ResetLinkError:
                    return "refused"

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(one, passwords))
        self.assertEqual(outcomes.count("changed"), 1)
        self.assertEqual(self.changed_email.call_count, 1)

    def test_an_activation_link_cannot_be_used_as_a_reset_link(self):
        _, token, _ = self.make_pending(email="new@example.com", household="H-2")
        self.assertEqual(self.client.get(f"/portal/reset/{token}").status_code, 410)

    def test_the_reset_page_closes_without_a_secret_key(self):
        _, token = self.start_reset()
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = None
        self.assertEqual(self.client.get(f"/portal/reset/{token}").status_code, 503)


class SeparationTests(ResetTestCase):
    def test_reset_pages_do_not_depend_on_or_create_a_staff_session(self):
        self.make_active()
        response = self.ask()
        names = [header for header in response.headers.getlist("Set-Cookie")]
        self.assertTrue(all("tanglaw_staff_session" not in value for value in names))
        with self.client.session_transaction() as staff_session:
            self.assertNotIn("user_id", staff_session)

    def test_the_log_filter_hides_reset_tokens_in_request_addresses(self):
        import logging
        from services.assessment_request_logging import PortalTokenFilter
        record = logging.LogRecord("werkzeug", logging.INFO, "", 0,
                                   '"GET /portal/reset/SECRETTOKEN123 HTTP/1.1" 200', None, None)
        PortalTokenFilter().filter(record)
        self.assertNotIn("SECRETTOKEN123", record.getMessage())


if __name__ == "__main__":
    unittest.main()
