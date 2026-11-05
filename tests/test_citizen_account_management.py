"""Staff deactivate, reactivate, change email and account history.

Service tests use an isolated schema in the test database with email delivery
mocked. Route tests use mocked services. Nothing touches the development database.
"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import psycopg

from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from repositories import citizen_accounts as repository
from routes import citizen_accounts as routes
from services import assessment_details as details
from services import citizen_accounts as service
from services import citizen_authentication as authentication
from services import staff_reviews as review_service
from services.citizen_account_panel import present_history
from services.citizen_credentials import new_reset_credentials
from services.email_delivery import EmailDeliveryError

import test_citizen_account_creation as creation
from test_citizen_account_creation import HOUSEHOLD, NOTE, TEST_URL
from test_citizen_account_routes import (
    ASSESSMENT, FORM, RouteTestCase, account_row, household_page,
)

DEACTIVATE = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/deactivate"
REACTIVATE = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/reactivate"
CHANGE_EMAIL = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/change-email"
HISTORY = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/history"
PASSWORD = "correct horse battery"
OLD_EMAIL = "citizen@example.com"
NEW_EMAIL = "new.address@example.com"
REASON = "The representative asked us to close the account."
EVENT_TIME = datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)


def management_form(**changes):
    form = {
        "representative_verified": "on", "privacy_consent_confirmed": "on",
        "verification_note": NOTE, "reason_note": REASON,
        "email": NEW_EMAIL, "email_confirmation": NEW_EMAIL,
    }
    form.update(changes)
    return {key: value for key, value in form.items() if value is not None}


class FormValidationTests(unittest.TestCase):
    def test_deactivation_needs_only_a_reason_of_ten_to_five_hundred_characters(self):
        self.assertEqual(service.validate_deactivation_form({"reason_note": f"  {REASON} "}),
                         {"note": REASON})
        for note in ("", "short", "x" * 501, "ok" * 5 + "\x00"):
            with self.subTest(note=note[:12]):
                with self.assertRaises(service.CitizenAccountInputError) as caught:
                    service.validate_deactivation_form({"reason_note": note})
                self.assertEqual(set(caught.exception.errors), {"reason_note"})

    def test_reactivation_and_email_change_need_both_confirmations_and_a_note(self):
        for validate in (service.validate_reactivation_form, service.validate_email_change_form):
            with self.subTest(validate=validate.__name__):
                with self.assertRaises(service.CitizenAccountInputError) as caught:
                    validate({})
                self.assertTrue({"representative_verified", "privacy_consent_confirmed",
                                 "verification_note"} <= set(caught.exception.errors))

    def test_the_new_email_must_be_valid_and_confirmed(self):
        for change in ({"email": "no-at-sign"}, {"email_confirmation": "other@example.com"}):
            with self.assertRaises(service.CitizenAccountInputError):
                service.validate_email_change_form(management_form(**change))
        cleaned = service.validate_email_change_form(management_form(email=" NEW.Address@Example.com ",
                                                                     email_confirmation=NEW_EMAIL))
        self.assertEqual(cleaned["email"], NEW_EMAIL)

    def test_history_is_newest_first_with_readable_names_and_no_addresses(self):
        events = [
            {"action": "created", "note": "First", "representative_verified": True,
             "privacy_consent_confirmed": True, "created_at": EVENT_TIME,
             "staff_display_name": None, "staff_username": "synthetic_staff"},
            {"action": "email_changed", "note": "Second", "representative_verified": True,
             "privacy_consent_confirmed": True, "created_at": EVENT_TIME,
             "staff_display_name": "Synthetic Staff", "staff_username": "synthetic_staff"},
        ]
        shown = present_history(events)
        self.assertEqual([entry["action_label"] for entry in shown],
                         ["Email address changed", "Account created"])
        self.assertEqual([entry["staff_name"] for entry in shown],
                         ["Synthetic Staff", "synthetic_staff"])


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class AccountManagementTests(unittest.TestCase):
    # Reuse the isolated-schema fixture from the creation tests without re-running them.
    _creation_setup = creation.AccountCreationTests.setUp
    _drop_schema = creation.AccountCreationTests._drop_schema
    _connect = creation.AccountCreationTests._connect
    _seed = creation.AccountCreationTests._seed
    _record_send = creation.AccountCreationTests._record_send
    counts = creation.AccountCreationTests.counts
    error_code = creation.AccountCreationTests.error_code

    def setUp(self):
        self._creation_setup()
        patcher = patch.object(service, "send_email_changed_notice")
        self.notice = patcher.start()
        self.addCleanup(patcher.stop)

    # --- helpers -----------------------------------------------------------

    def create(self):
        return service.create_citizen_account(
            self.staff_id, HOUSEHOLD, self.batch, self.run, creation.valid_form(),
            connection_factory=self._connect,
        )

    def activate(self):
        """Create an account and activate it the way the citizen would."""
        result = self.create()
        token = self.sent[-1]["token"]
        authentication.activate_account(
            token, result["activation_code"], PASSWORD, PASSWORD,
            connection_factory=self._connect,
        )
        return self.account()

    def account(self):
        with self._connect() as connection:
            return repository.find_by_email(connection, OLD_EMAIL) or \
                repository.find_by_email(connection, NEW_EMAIL)

    def manage(self, function, form=None, staff=None, **keywords):
        return function(
            staff or self.staff_id, HOUSEHOLD, self.batch, self.run,
            management_form() if form is None else form,
            connection_factory=self._connect, **keywords,
        )

    def deactivate(self, **keywords):
        return self.manage(service.deactivate_account, **keywords)

    def reactivate(self, **keywords):
        return self.manage(service.reactivate_account, **keywords)

    def change_email(self, **keywords):
        return self.manage(service.change_account_email, **keywords)

    def open_tokens(self, purpose=None):
        return self.connection.execute(
            "SELECT count(*) FROM citizen_account_tokens WHERE used_at IS NULL"
            " AND revoked_at IS NULL AND (%s::text IS NULL OR purpose = %s::text)",
            (purpose, purpose)).fetchone()[0]

    def add_reset_link(self, account_id):
        credentials = new_reset_credentials()
        with self._connect() as connection:
            repository.insert_token(connection, account_id, "password_reset",
                                    credentials.token_hash, None, timedelta(hours=1))

    def last_event(self):
        return self.connection.execute(
            "SELECT action, note, staff_id, representative_verified, privacy_consent_confirmed"
            " FROM citizen_account_events ORDER BY id DESC LIMIT 1").fetchone()

    def sign_in(self, email=OLD_EMAIL):
        return authentication.sign_in(email, PASSWORD, connection_factory=self._connect)

    # --- deactivate --------------------------------------------------------

    def test_deactivating_ends_sessions_links_and_sign_in_and_records_the_reason(self):
        account = self.activate()
        self.add_reset_link(account["id"])
        self.assertEqual(self.sign_in()["id"], account["id"])
        self.deactivate()
        after = self.account()
        self.assertEqual(after["status"], "deactivated")
        self.assertGreater(after["session_version"], account["session_version"])
        self.assertEqual((after["email"], after["household_uuid"]), (OLD_EMAIL, HOUSEHOLD))
        self.assertEqual(self.open_tokens(), 0)
        with self.assertRaises(authentication.SignInError):
            self.sign_in()
        self.assertEqual(self.last_event(), ("deactivated", REASON, self.staff_id, False, False))

    def test_a_pending_account_can_be_deactivated_and_its_link_stops_working(self):
        self.create()
        token = self.sent[-1]["token"]
        self.deactivate()
        self.assertFalse(authentication.activation_link_is_usable(
            token, connection_factory=self._connect))
        self.assertEqual(self.account()["status"], "deactivated")

    def test_deactivating_sends_no_email(self):
        self.activate()
        sent_before = len(self.sent)
        self.deactivate()
        self.assertEqual(len(self.sent), sent_before)
        self.notice.assert_not_called()

    def test_deactivation_needs_a_reason_and_a_live_account(self):
        self.create()
        with self.assertRaises(service.CitizenAccountInputError):
            self.deactivate(form={"reason_note": "short"})
        self.assertEqual(self.account()["status"], "pending_activation")
        self.deactivate()
        self.assertEqual(self.error_code(self.deactivate), "not_deactivatable")
        self.assertEqual(self.counts()[2], 2)

    # --- reactivate --------------------------------------------------------

    def test_reactivating_clears_the_password_and_sends_a_new_link_and_code(self):
        account = self.activate()
        self.deactivate()
        old_token = self.sent[-1]["token"]
        result = self.reactivate()
        after = self.account()
        self.assertEqual((after["status"], after["email"]), ("pending_activation", OLD_EMAIL))
        self.assertIsNone(self.connection.execute(
            "SELECT password_hash FROM citizen_accounts WHERE id = %s",
            (account["id"],)).fetchone()[0])
        self.assertGreater(after["session_version"], account["session_version"])
        self.assertEqual(self.open_tokens("activation"), 1)
        self.assertTrue(result["email_sent"])
        self.assertRegex(result["activation_code"], r"^\d{3} \d{3}$")
        self.assertNotEqual(self.sent[-1]["token"], old_token)
        self.assertTrue(self.sent[-1]["committed_when_sent"])
        self.assertEqual(self.last_event(), ("reactivated", NOTE, self.staff_id, True, True))
        with self.assertRaises(authentication.SignInError):
            self.sign_in()

    def test_the_new_code_activates_the_account_with_a_new_password(self):
        self.activate()
        self.deactivate()
        result = self.reactivate()
        authentication.activate_account(
            self.sent[-1]["token"], result["activation_code"], "a brand new passphrase",
            "a brand new passphrase", connection_factory=self._connect)
        self.assertEqual(self.account()["status"], "active")
        self.assertEqual(authentication.sign_in(
            OLD_EMAIL, "a brand new passphrase", connection_factory=self._connect)["email"],
            OLD_EMAIL)

    def test_a_failed_reactivation_email_keeps_the_change_and_shows_no_code(self):
        self.activate()
        self.deactivate()
        self.send.side_effect = EmailDeliveryError("connection_failed")
        result = self.reactivate()
        self.assertFalse(result["email_sent"])
        self.assertIsNone(result["activation_code"])
        self.assertEqual(self.account()["status"], "pending_activation")

    def test_only_a_deactivated_account_can_be_reactivated(self):
        self.activate()
        self.assertEqual(self.error_code(self.reactivate), "not_deactivated")
        self.assertEqual(self.account()["status"], "active")

    def test_reactivation_needs_both_confirmations(self):
        self.create()
        self.deactivate()
        for missing in ("representative_verified", "privacy_consent_confirmed"):
            with self.assertRaises(service.CitizenAccountInputError):
                self.reactivate(form=management_form(**{missing: None}))
        self.assertEqual(self.account()["status"], "deactivated")

    # --- change email ------------------------------------------------------

    def test_changing_email_requires_activation_again_at_the_new_address_only(self):
        account = self.activate()
        self.add_reset_link(account["id"])
        sent_before = len(self.sent)
        result = self.change_email()
        after = self.account()
        self.assertEqual((after["status"], after["email"]), ("pending_activation", NEW_EMAIL))
        self.assertGreater(after["session_version"], account["session_version"])
        self.assertEqual(self.open_tokens("password_reset"), 0)
        self.assertEqual(self.open_tokens("activation"), 1)
        self.assertEqual([item["address"] for item in self.sent[sent_before:]], [NEW_EMAIL])
        self.assertTrue(self.sent[-1]["committed_when_sent"])
        self.assertTrue(result["email_sent"])
        self.assertEqual(self.last_event(), ("email_changed", NOTE, self.staff_id, True, True))
        with self.assertRaises(authentication.SignInError):
            self.sign_in(NEW_EMAIL)

    def test_the_old_address_gets_a_notice_that_names_nothing_else(self):
        self.activate()
        result = self.change_email()
        self.notice.assert_called_once_with(OLD_EMAIL)
        self.assertTrue(result["previous_email_notified"])

    def test_a_failed_notice_does_not_undo_the_change(self):
        self.activate()
        self.notice.side_effect = EmailDeliveryError("connection_failed")
        result = self.change_email()
        self.assertFalse(result["previous_email_notified"])
        self.assertTrue(result["email_sent"])
        self.assertEqual(self.account()["email"], NEW_EMAIL)

    def test_a_pending_account_can_change_email_and_the_old_link_stops_working(self):
        self.create()
        old_token = self.sent[-1]["token"]
        self.change_email()
        self.assertFalse(authentication.activation_link_is_usable(
            old_token, connection_factory=self._connect))
        self.assertTrue(authentication.activation_link_is_usable(
            self.sent[-1]["token"], connection_factory=self._connect))

    def test_a_deactivated_account_must_be_reactivated_before_changing_email(self):
        self.create()
        self.deactivate()
        self.assertEqual(self.error_code(self.change_email), "deactivated")
        self.assertEqual(self.account()["email"], OLD_EMAIL)
        self.notice.assert_not_called()

    def test_the_same_address_or_one_used_elsewhere_changes_nothing(self):
        self.create()
        same = management_form(email=OLD_EMAIL, email_confirmation=OLD_EMAIL)
        with self.assertRaises(service.CitizenAccountInputError) as caught:
            self.change_email(form=same)
        self.assertIn("email", caught.exception.errors)
        self.connection.execute(
            "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
            " VALUES (%s, 'other-household', 9, '{}')", (self.batch,))
        self.connection.execute(
            "INSERT INTO citizen_accounts (batch_id, household_uuid, representative_name, email)"
            " VALUES (%s, 'other-household', 'Other Person', %s)", (self.batch, NEW_EMAIL))
        sent_before = len(self.sent)
        self.assertEqual(self.error_code(self.change_email), "email_in_use")
        self.assertEqual(self.account()["email"], OLD_EMAIL)
        self.assertEqual(len(self.sent), sent_before)
        self.notice.assert_not_called()
        self.assertEqual(self.open_tokens("activation"), 1)

    # --- permissions and context ------------------------------------------

    def test_only_active_lgu_staff_may_manage_accounts(self):
        self.create()
        for actor in (self.admin_id, self.inactive_id):
            for function in (self.deactivate, self.reactivate, self.change_email):
                with self.subTest(actor=actor, function=function.__name__):
                    self.assertEqual(self.error_code(function, staff=actor), "unauthorized")
        self.assertEqual(self.account()["status"], "pending_activation")
        self.assertEqual(self.counts()[2], 1)

    def test_the_selected_survey_must_still_match_and_the_account_must_exist(self):
        for function in (self.deactivate, self.reactivate, self.change_email):
            self.assertEqual(self.error_code(function), "account_missing")
        self.create()
        self.assertEqual(self.error_code(
            service.deactivate_account, self.staff_id, HOUSEHOLD, self.batch, self.run + 1,
            management_form(), connection_factory=self._connect), "context_changed")

    # --- history -----------------------------------------------------------

    def test_history_lists_every_staff_action_newest_first(self):
        self.activate()
        self.deactivate()
        self.reactivate()
        page = service.get_household_account_page(
            HOUSEHOLD, with_history=True, connection_factory=self._connect)
        labels = [entry["action_label"] for entry in page["history"]]
        self.assertEqual(labels, ["Account reactivated", "Account deactivated", "Account created"])
        self.assertEqual({entry["staff_name"] for entry in page["history"]}, {"synthetic_staff"})
        self.assertNotIn("history", service.get_household_account_page(
            HOUSEHOLD, connection_factory=self._connect))

    def test_history_cannot_be_edited_or_removed(self):
        self.create()
        with self.assertRaises(psycopg.Error):
            self.connection.execute("DELETE FROM citizen_account_events")


class ManagementRouteCase(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.page = household_page(account_row("active"))
        self.done = {"email": NEW_EMAIL, "email_sent": True, "email_problem": None,
                     "activation_code": "482 913", "valid_hours": 48,
                     "previous_email_notified": True}
        for name in ("deactivate_account", "reactivate_account", "change_account_email"):
            patcher = patch.object(routes, name, return_value=self.done)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def form_for(self, path, **changes):
        """Open the form like a browser and return the data it would post."""
        self.sign_in()
        page = self.client.get(path)
        number = page.get_data(as_text=True).split('name="form_number" value="')[1].split('"')[0]
        return {**FORM, "reason_note": REASON, "email": NEW_EMAIL,
                "email_confirmation": NEW_EMAIL, "form_number": number, **changes}


class ManagementAuthorizationTests(ManagementRouteCase):
    PATHS = (DEACTIVATE, REACTIVATE, CHANGE_EMAIL, HISTORY)

    @staticmethod
    def methods_for(path):
        """The history page only reads, so it accepts GET alone."""
        return ("get",) if path == HISTORY else ("get", "post")

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        for path in self.PATHS:
            for method in self.methods_for(path):
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, data=FORM)
                    self.assertEqual((response.status_code, response.location), (302, "/login"))
        self.mocks["page"].assert_not_called()

    def test_administrators_are_refused_everywhere(self):
        self.sign_in("system_administrator")
        for path in self.PATHS:
            for method in self.methods_for(path):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path, data=FORM).status_code, 403)
        for name in ("deactivate_account", "reactivate_account", "change_account_email"):
            self.mocks[name].assert_not_called()

    def test_every_form_post_needs_a_valid_csrf_token(self):
        for path, status, name in ((DEACTIVATE, "active", "deactivate_account"),
                                   (REACTIVATE, "deactivated", "reactivate_account"),
                                   (CHANGE_EMAIL, "active", "change_account_email")):
            with self.subTest(path=path):
                self.page = household_page(account_row(status))
                data = self.form_for(path, csrf_token="forged")
                self.assertEqual(self.client.post(path, data=data).status_code, 400)
                self.mocks[name].assert_not_called()

    def test_account_history_is_read_only(self):
        self.sign_in()
        self.assertEqual(self.client.get(HISTORY).status_code, 200)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(HISTORY, data=FORM)
                self.assertEqual(response.status_code, 405)
        for name in ("deactivate_account", "reactivate_account", "change_account_email"):
            self.mocks[name].assert_not_called()


class ManagementFormTests(ManagementRouteCase):
    def test_each_form_opens_only_for_the_states_that_allow_it(self):
        cases = (
            (DEACTIVATE, "active", True), (DEACTIVATE, "pending_activation", True),
            (DEACTIVATE, "deactivated", False),
            (CHANGE_EMAIL, "active", True), (CHANGE_EMAIL, "pending_activation", True),
            (CHANGE_EMAIL, "deactivated", False),
            (REACTIVATE, "deactivated", True), (REACTIVATE, "active", False),
            (REACTIVATE, "pending_activation", False),
        )
        self.sign_in()
        for path, status, opens in cases:
            with self.subTest(path=path, status=status):
                self.page = household_page(account_row(status))
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200 if opens else 303)
                if not opens:
                    self.assertEqual(response.location, ASSESSMENT)

    def test_a_household_without_an_account_returns_to_the_assessment(self):
        self.page = household_page("none")
        self.sign_in()
        for path in (DEACTIVATE, REACTIVATE, CHANGE_EMAIL, HISTORY):
            self.assertEqual(self.client.get(path).location, ASSESSMENT)

    def test_the_deactivation_page_explains_the_consequences_before_the_button(self):
        self.sign_in()
        text = self.main_text(self.client.get(DEACTIVATE))
        for words in ("signed out immediately", "activation or password reset link",
                      "reactivate it later", "Why is this account being deactivated?"):
            self.assertIn(words, text)
        self.assertIn("account-button-danger", self.text(self.client.get(DEACTIVATE)))
        self.assertNotIn('type="checkbox"', text)

    def test_the_reactivation_and_email_pages_ask_for_both_confirmations(self):
        for path, status in ((REACTIVATE, "deactivated"), (CHANGE_EMAIL, "active")):
            self.page = household_page(account_row(status))
            self.sign_in()
            text = self.main_text(self.client.get(path))
            self.assertEqual(text.count('type="checkbox"'), 2)
            self.assertIn("48 hours" if path == REACTIVATE else "short notice", text)

    def test_the_email_page_shows_the_current_address_and_asks_for_the_new_one_twice(self):
        self.sign_in()
        text = self.main_text(self.client.get(CHANGE_EMAIL))
        self.assertIn("Current email address", text)
        self.assertIn('name="email_confirmation"', text)


class ManagementSubmissionTests(ManagementRouteCase):
    def test_deactivating_returns_to_the_assessment_with_a_message(self):
        response = self.client.post(DEACTIVATE, data=self.form_for(DEACTIVATE))
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.mocks["deactivate_account"].assert_called_once()
        self.assertEqual(self.mocks["deactivate_account"].call_args.args[:4], (5, HOUSEHOLD_UUID, 3, 7))
        with self.client.session_transaction() as session:
            self.assertIn("deactivated", dict(session["_flashes"])["citizen_account"])

    def test_reactivating_shows_the_code_once_and_never_the_link(self):
        self.page = household_page(account_row("deactivated"))
        response = self.client.post(REACTIVATE, data=self.form_for(REACTIVATE))
        text = self.text(response)
        self.assertIn("Citizen Account reactivated", text)
        self.assertIn("482 913", text)
        self.assertNotIn("/portal/", text)

    def test_changing_email_reports_the_notice_to_the_old_address(self):
        response = self.client.post(CHANGE_EMAIL, data=self.form_for(CHANGE_EMAIL))
        self.assertIn("Email address changed", self.text(response))
        # The staff sidebar hides .account-notice on small screens, so this page must not use it.
        self.assertIn('class="citizen-account-notice"', self.main_text(response))
        self.assertNotIn('class="account-notice"', self.main_text(response))
        self.assertIn("notice was sent to the previous email address", self.text(response))
        self.done["previous_email_notified"] = False
        again = self.client.post(CHANGE_EMAIL, data=self.form_for(CHANGE_EMAIL))
        self.assertIn("could not be sent. The change is saved", self.text(again))

    def test_a_failed_email_shows_no_code(self):
        self.done.update(email_sent=False, activation_code=None)
        response = self.client.post(CHANGE_EMAIL, data=self.form_for(CHANGE_EMAIL))
        self.assertIn("Activation email not sent", self.text(response))
        self.assertNotIn("482 913", self.text(response))

    def test_form_mistakes_show_the_form_again_with_a_fresh_number(self):
        errors = {"reason_note": "Write a short note of 10 to 500 characters."}
        self.mocks["deactivate_account"].side_effect = service.CitizenAccountInputError(errors)
        data = self.form_for(DEACTIVATE)
        response = self.client.post(DEACTIVATE, data=data)
        self.assertEqual(response.status_code, 400)
        self.assertIn(errors["reason_note"], self.text(response))
        self.assertNotIn(f'value="{data["form_number"]}"', self.text(response))

    def test_an_email_used_elsewhere_keeps_the_form_open(self):
        self.mocks["change_account_email"].side_effect = service.CitizenAccountError("email_in_use")
        response = self.client.post(CHANGE_EMAIL, data=self.form_for(CHANGE_EMAIL))
        self.assertEqual(response.status_code, 409)
        self.assertIn("already used by another Citizen Account", self.text(response))

    def test_a_status_that_changed_since_the_form_opened_returns_to_the_assessment(self):
        for name, code in (("deactivate_account", "not_deactivatable"),
                           ("reactivate_account", "not_deactivated"),
                           ("change_account_email", "deactivated")):
            self.mocks[name].side_effect = service.CitizenAccountError(code)
        cases = ((DEACTIVATE, "active"), (REACTIVATE, "deactivated"), (CHANGE_EMAIL, "active"))
        for path, status in cases:
            with self.subTest(path=path):
                self.page = household_page(account_row(status))
                response = self.client.post(path, data=self.form_for(path))
                self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))

    def test_a_refresh_or_double_click_acts_only_once(self):
        data = self.form_for(DEACTIVATE)
        statuses = [self.client.post(DEACTIVATE, data=data).status_code for _ in range(3)]
        self.assertEqual(statuses, [303, 303, 303])
        self.assertEqual(self.mocks["deactivate_account"].call_count, 1)

    def test_unauthorized_and_unexpected_failures_stop_with_plain_pages(self):
        self.mocks["deactivate_account"].side_effect = service.CitizenAccountError("unauthorized")
        self.assertEqual(self.client.post(DEACTIVATE, data=self.form_for(DEACTIVATE)).status_code, 403)
        self.mocks["deactivate_account"].side_effect = service.CitizenAccountError("unavailable")
        self.assertEqual(self.client.post(DEACTIVATE, data=self.form_for(DEACTIVATE)).status_code, 503)


class HistoryPageTests(ManagementRouteCase):
    def test_the_page_lists_actions_with_staff_names_dates_and_notes(self):
        events = [
            {"action": "created", "note": "Checked a valid ID.", "representative_verified": True,
             "privacy_consent_confirmed": True, "created_at": EVENT_TIME,
             "staff_display_name": None, "staff_username": "synthetic_staff"},
            {"action": "deactivated", "note": REASON, "representative_verified": False,
             "privacy_consent_confirmed": False, "created_at": EVENT_TIME,
             "staff_display_name": "Synthetic Staff", "staff_username": "synthetic_staff"},
        ]
        self.page["history"] = present_history(events)
        self.sign_in()
        text = self.main_text(self.client.get(HISTORY))
        self.assertLess(text.index("Account deactivated"), text.index("Account created"))
        for words in ("By Synthetic Staff", "By synthetic_staff", REASON, "Checked a valid ID.",
                      "Representative verified and consent confirmed"):
            self.assertIn(words, text)
        self.assertNotIn("citizen@example.com", text)
        self.assertNotIn("/portal/", text)

    def test_an_account_without_history_says_so(self):
        self.page["history"] = []
        self.sign_in()
        self.assertIn("No activity has been recorded yet.", self.text(self.client.get(HISTORY)))


class PanelButtonTests(ManagementRouteCase):
    def setUp(self):
        super().setUp()
        self.source = detail_source()
        for target in (details, review_service):
            patcher = patch.object(target, "get_active_assessment_detail", return_value=self.source)
            patcher.start()
            self.addCleanup(patcher.stop)

    def links(self, status, role="lgu_staff"):
        self.source["citizen_account"] = account_row(status)
        self.sign_in(role)
        text = self.text(self.client.get(ASSESSMENT))
        return {path for path in (DEACTIVATE, REACTIVATE, CHANGE_EMAIL, HISTORY)
                if f'href="{path}"' in text}

    def test_each_status_offers_only_its_own_actions(self):
        self.assertEqual(self.links("pending_activation"), {DEACTIVATE, CHANGE_EMAIL, HISTORY})
        self.assertEqual(self.links("active"), {DEACTIVATE, CHANGE_EMAIL, HISTORY})
        self.assertEqual(self.links("deactivated"), {REACTIVATE, HISTORY})

    def test_administrators_see_no_account_actions(self):
        for status in ("pending_activation", "active", "deactivated"):
            self.assertEqual(self.links(status, "system_administrator"), set())


if __name__ == "__main__":
    unittest.main()
