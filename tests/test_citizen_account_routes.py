"""Staff pages for Citizen Accounts: authorization, forms and what staff can see."""

from datetime import datetime, timedelta, timezone
from html import unescape
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from routes import citizen_accounts as routes
from routes import staff_forms
from services import assessment_details as details
from services import staff_reviews as review_service
from services.citizen_account_panel import present_account_panel
from services.citizen_accounts import (
    CitizenAccountError, CitizenAccountInputError, validate_creation_form,
)

CREATE = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/new"
RESEND = f"/citizen-accounts/household/{HOUSEHOLD_UUID}/resend"
ASSESSMENT = f"/assessments/{HOUSEHOLD_UUID}"
NOW = datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)
SECRET_LINK_PART = "/portal/activate/"
FORM = {
    "csrf_token": "synthetic-csrf", "batch_id": "3", "run_id": "7",
    "representative_name": "Synthetic Citizen", "email": "citizen@example.com",
    "email_confirmation": "citizen@example.com", "representative_verified": "on",
    "privacy_consent_confirmed": "on",
    "verification_note": "Spoke with the representative by phone today.",
}


def account_row(status="pending_activation", **changes):
    row = {
        "status": status, "representative_name": "Synthetic Citizen",
        "email": "citizen@example.com", "created_at": NOW, "activated_at": None,
        "email_accepted_at": NOW, "link_valid": True, "expires_at": NOW + timedelta(hours=48),
    }
    if status == "active":
        row["activated_at"] = NOW
    row.update(changes)
    return row


def household_page(account=None):
    return {
        "batch_id": 3, "run_id": 7, "household_uuid": HOUSEHOLD_UUID,
        "reference_id": "HH-0008", "head_name": "Synthetic Head",
        "barangay": "Synthetic Barangay", "contact_number": "0912 345 6789",
        "account": present_account_panel(account) if account != "none" and account
        else {"state": "none"},
    }


class RouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 5, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        self.page = household_page()
        patches = {
            "connect": patch("psycopg.connect", side_effect=AssertionError("No real database")),
            "auth": patch("services.auth.get_connection", return_value=MagicMock()),
            "user": patch("repositories.staff_users.find_by_id",
                          side_effect=lambda *args: dict(self.user)),
            "page": patch.object(routes, "get_household_account_page",
                                 side_effect=lambda *args, **kw: self.page),
            "create": patch.object(routes, "create_citizen_account"),
            "resend": patch.object(routes, "resend_activation"),
        }
        self.mocks = {}
        for name, patcher in patches.items():
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.sent_result = {
            "email": "citizen@example.com", "email_sent": True, "email_problem": None,
            "activation_code": "482 913", "valid_hours": 48,
        }
        self.mocks["create"].return_value = self.sent_result
        self.mocks["resend"].return_value = self.sent_result

    def sign_in(self, role="lgu_staff"):
        self.user["role"] = role
        with self.client.session_transaction() as session:
            session.update(user_id=5, session_version=1, issued_at=int(time()),
                           csrf_token="synthetic-csrf")

    def submit(self, path, data=None):
        """POST like a browser: with a fresh one-time form number remembered in the session."""
        self.form_count = getattr(self, "form_count", 0) + 1
        number = f"synthetic-form-{self.form_count}"
        with self.client.session_transaction() as session:
            session[staff_forms.FORM_NUMBERS_KEY] = session.get(staff_forms.FORM_NUMBERS_KEY, []) + [number]
        return self.client.post(path, data={**(FORM if data is None else data),
                                            "form_number": number})

    def text(self, response):
        return unescape(response.get_data(as_text=True))

    def main_text(self, response):
        """Only the page's own content, without the layout's sign-out form and its CSRF field."""
        match = re.search(r"<main.*?</main>", self.text(response), re.S)
        return match.group(0)


class AuthorizationTests(RouteTestCase):
    def test_anonymous_visitors_are_sent_to_sign_in_on_every_route_and_method(self):
        rules = [rule for rule in self.app.url_map.iter_rules()
                 if rule.endpoint.startswith("citizen_accounts.")]
        self.assertEqual(len(rules), 6)
        for rule in rules:
            path = re.sub(r"<[^>]+>", HOUSEHOLD_UUID, rule.rule)
            for method in ("get", "post"):
                if method.upper() not in rule.methods:
                    continue  # The history page only reads.
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, data=FORM)
                    self.assertEqual((response.status_code, response.location), (302, "/login"))
        for mock in ("page", "create", "resend"):
            self.mocks[mock].assert_not_called()

    def test_administrators_are_refused_on_every_route_and_method(self):
        self.sign_in("system_administrator")
        for path in (CREATE, RESEND):
            for method in ("get", "post"):
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path, data=FORM).status_code, 403)
        for mock in ("page", "create", "resend"):
            self.mocks[mock].assert_not_called()

    def test_inactive_revoked_and_expired_sessions_are_refused(self):
        for field, value in (("is_active", False), ("session_version", 2), ("role", "other")):
            with self.subTest(field=field), patch.dict(self.user, {field: value}):
                self.sign_in()
                self.user[field] = value
                self.assertEqual(self.client.get(CREATE).location, "/login")
        self.sign_in()
        with self.client.session_transaction() as session:
            session["issued_at"] = 0
        self.assertEqual(self.client.get(CREATE).location, "/login")
        self.mocks["page"].assert_not_called()

    def test_posts_without_the_csrf_token_are_rejected_before_any_action(self):
        self.sign_in()
        for path in (CREATE, RESEND):
            for data in ({**FORM, "csrf_token": "wrong"}, {k: v for k, v in FORM.items() if k != "csrf_token"}):
                with self.subTest(path=path):
                    self.assertEqual(self.client.post(path, data=data).status_code, 400)
        self.mocks["create"].assert_not_called()
        self.mocks["resend"].assert_not_called()

    def test_the_acting_staff_member_comes_from_the_session_not_the_form(self):
        self.sign_in()
        self.submit(CREATE, {**FORM, "staff_id": "999", "created_by": "999"})
        arguments = self.mocks["create"].call_args.args
        self.assertEqual(arguments[:4], (5, HOUSEHOLD_UUID, 3, 7))


class CreateFormTests(RouteTestCase):
    def test_form_shows_the_household_and_the_approved_wording(self):
        self.sign_in()
        response = self.client.get(CREATE)
        text = self.text(response)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Synthetic Head", text)
        self.assertIn("HH-0008", text)
        self.assertIn("0912 345 6789", text)
        for sentence in (
            "I spoke with the household representative and confirmed they can act for this household.",
            "The representative agreed to create this account and to receive emails at the address "
            "above, and confirmed I read it back correctly.",
            "How did you confirm the representative's identity or authority?",
            "Tanglaw-Buhay will keep your name and email address to create and run your online "
            "account. It will use your email address to send account messages, such as your "
            "activation link and password reset links.",
            "Create Account",
            "Creating an account does not start a Staff Review",
        ):
            self.assertIn(sentence, text)
        self.assertNotRegex(text.lower(), r"\bnever send\b|\bonly send\b|will not send other")

    def test_every_field_has_a_label_and_the_checkboxes_are_required_choices(self):
        self.sign_in()
        html = self.client.get(CREATE).get_data(as_text=True)
        for name in ("representative_name", "email", "email_confirmation", "representative_verified",
                     "privacy_consent_confirmed", "verification_note"):
            self.assertRegex(html, rf'id="{name}"')
            self.assertRegex(html, rf'<label for="{name}">')
        self.assertEqual(len(re.findall(r'type="checkbox"', html)), 2)
        self.assertNotIn("checked", re.sub(r'type="checkbox"', "", html).replace("aria-", ""))
        self.assertIn('name="csrf_token" value="synthetic-csrf"', html)
        self.assertIn('name="batch_id" value="3"', html)
        self.assertIn('name="run_id" value="7"', html)

    def test_pages_are_never_cached(self):
        self.sign_in()
        self.assertEqual(self.client.get(CREATE).headers["Cache-Control"], "no-store")

    def test_a_household_that_already_has_an_account_is_sent_back_with_a_message(self):
        self.sign_in()
        self.page = household_page(account_row())
        response = self.client.get(CREATE)
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        with self.client.session_transaction() as session:
            flashed = dict(session.get("_flashes", []))
        self.assertEqual(flashed, {"citizen_account": "This household already has a Citizen Account."})

    def test_unavailable_accounts_stop_the_form_with_a_clear_page(self):
        self.sign_in()
        self.page = {**household_page(), "account": {"state": "unavailable"}}
        response = self.client.get(CREATE)
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", self.text(response))

    def test_unknown_households_give_a_not_found_page(self):
        from services.assessment_queries import AssessmentQueryError
        self.sign_in()
        self.mocks["page"].side_effect = AssessmentQueryError("household_assessment_missing")
        self.assertEqual(self.client.get(CREATE).status_code, 404)
        self.mocks["page"].side_effect = AssessmentQueryError("assessment_query_unavailable")
        self.assertEqual(self.client.get(CREATE).status_code, 503)


class CreateSubmissionTests(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()

    def test_success_shows_the_code_once_and_never_a_link(self):
        response = self.submit(CREATE)
        text = self.main_text(response)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Citizen Account created", text)
        self.assertIn("482 913", text)
        self.assertIn("This code is shown only once.", text)
        self.assertIn("citizen@example.com", text)
        self.assertIn("The activation email was sent to", text)
        self.assertIn("48 hours", text)
        self.assertNotIn(SECRET_LINK_PART, text)
        self.assertNotIn("token", text.lower())
        self.assertNotRegex(text, r"https?://")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_the_service_receives_the_form_and_the_selection_that_was_shown(self):
        self.submit(CREATE)
        arguments = self.mocks["create"].call_args.args
        self.assertEqual(arguments[:4], (5, HOUSEHOLD_UUID, 3, 7))
        self.assertEqual(arguments[4]["email"], "citizen@example.com")

    def test_a_failed_email_explains_what_happened_and_shows_no_code(self):
        self.mocks["create"].return_value = {
            **self.sent_result, "email_sent": False, "activation_code": None,
            "email_problem": "The email server could not be reached in time.",
        }
        text = self.main_text(self.submit(CREATE))
        self.assertIn("The activation email was not sent", text)
        self.assertIn("no activation code is shown", text)
        self.assertIn("Resend Activation", text)
        self.assertNotIn("Activation code", text)
        # No six-digit code (the contact number has a different grouping).
        self.assertNotRegex(text, r"(?<!\d)\d{3} \d{3}(?!\d)")

    def test_form_errors_return_the_form_with_messages_and_the_entered_values(self):
        errors = {}
        try:
            validate_creation_form({**FORM, "email_confirmation": "other@example.com",
                                    "verification_note": "short", "privacy_consent_confirmed": ""})
        except CitizenAccountInputError as invalid:
            errors = invalid.errors
        self.mocks["create"].side_effect = CitizenAccountInputError(errors)
        response = self.submit(CREATE)
        text = self.text(response)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Please correct the items below", text)
        for message in errors.values():
            self.assertIn(message, text)
        self.assertIn('aria-invalid="true"', response.get_data(as_text=True))
        self.assertIn('value="Synthetic Citizen"', response.get_data(as_text=True))
        self.assertIn("Spoke with the representative by phone today.", text)

    def test_a_duplicate_household_goes_back_to_the_assessment_with_a_message(self):
        self.mocks["create"].side_effect = CitizenAccountError("household_has_account")
        response = self.submit(CREATE)
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))

    def test_a_duplicate_email_keeps_the_form_and_explains_the_problem(self):
        self.mocks["create"].side_effect = CitizenAccountError("email_in_use")
        response = self.submit(CREATE)
        self.assertEqual(response.status_code, 409)
        self.assertIn("already used by another Citizen Account", self.text(response))
        self.assertIn('aria-invalid="true"', response.get_data(as_text=True))

    def test_other_failures_map_to_clear_status_pages(self):
        for code, status in (("unauthorized", 403), ("household_missing", 404),
                             ("context_changed", 400), ("invalid_request", 400),
                             ("unavailable", 503)):
            with self.subTest(code=code):
                self.mocks["create"].side_effect = CitizenAccountError(code)
                self.assertEqual(self.submit(CREATE).status_code, status)

    def test_bad_selection_fields_stop_the_request_before_the_service(self):
        for change in ({"batch_id": ""}, {"run_id": "abc"}, {"batch_id": "-1"},
                       {"run_id": "9" * 30}, {"batch_id": "１２"}):
            with self.subTest(change=change):
                self.assertEqual(self.submit(CREATE, {**FORM, **change}).status_code, 400)
        duplicated = {**FORM, "batch_id": ["3", "4"]}
        self.assertEqual(self.submit(CREATE, duplicated).status_code, 400)
        self.mocks["create"].assert_not_called()


class ResendTests(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.sign_in()
        self.page = household_page(account_row())

    def test_confirmation_page_warns_that_the_old_link_and_code_stop_working(self):
        response = self.client.get(RESEND)
        text = self.text(response)
        self.assertEqual(response.status_code, 200)
        self.assertIn("The previous link and code will stop working immediately.", text)
        self.assertIn("citizen@example.com", text)
        self.assertIn("Send New Activation Email", text)
        self.mocks["resend"].assert_not_called()

    def test_resend_shows_the_new_code_once_and_no_link(self):
        response = self.submit(RESEND)
        text = self.text(response)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Activation email resent", text)
        self.assertIn("482 913", text)
        self.assertNotIn(SECRET_LINK_PART, text)
        self.assertEqual(self.mocks["resend"].call_args.args, (5, HOUSEHOLD_UUID, 3, 7))

    def test_resend_is_unavailable_without_a_pending_account(self):
        for page in (household_page(), household_page(account_row("active")),
                     household_page(account_row("deactivated"))):
            with self.subTest(state=page["account"]["state"]):
                self.page = page
                for method in ("get", "post"):
                    response = getattr(self.client, method)(RESEND, data=FORM)
                    self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.mocks["resend"].assert_not_called()

    def test_service_refusals_are_explained(self):
        for code, status in (("unauthorized", 403), ("household_missing", 404),
                             ("context_changed", 400), ("unavailable", 503)):
            with self.subTest(code=code):
                self.mocks["resend"].side_effect = CitizenAccountError(code)
                self.assertEqual(self.submit(RESEND).status_code, status)
        for code in ("not_pending", "account_missing"):
            with self.subTest(code=code):
                self.mocks["resend"].side_effect = CitizenAccountError(code)
                self.assertEqual(self.submit(RESEND).location, ASSESSMENT)


class RepeatedSubmissionTests(RouteTestCase):
    """A browser refresh or double click must never repeat an action or show a code twice."""

    def setUp(self):
        super().setUp()
        self.sign_in()

    def flashed(self):
        with self.client.session_transaction() as session:
            return dict(session.get("_flashes", []))

    def number_in(self, response):
        return re.search(r'name="form_number" value="([^"]+)"',
                         response.get_data(as_text=True)).group(1)

    def test_every_form_carries_its_own_one_time_number(self):
        first = self.number_in(self.client.get(CREATE))
        second = self.number_in(self.client.get(CREATE))
        self.page = household_page(account_row())
        third = self.number_in(self.client.get(RESEND))
        self.assertEqual(len({first, second, third}), 3)

    def test_only_the_most_recent_numbers_are_remembered(self):
        for _ in range(staff_forms.MAX_REMEMBERED_FORMS + 5):
            self.client.get(CREATE)
        with self.client.session_transaction() as session:
            self.assertEqual(len(session[staff_forms.FORM_NUMBERS_KEY]), staff_forms.MAX_REMEMBERED_FORMS)

    def test_refreshing_a_created_account_does_not_repeat_it_or_show_the_code_again(self):
        number = self.number_in(self.client.get(CREATE))
        data = {**FORM, "form_number": number}
        first = self.client.post(CREATE, data=data)
        self.assertIn("482 913", self.text(first))
        again = self.client.post(CREATE, data=data)
        self.assertEqual((again.status_code, again.location), (303, ASSESSMENT))
        self.assertNotIn("482 913", self.text(again))
        self.assertEqual(self.mocks["create"].call_count, 1)
        self.assertEqual(self.flashed(), {"citizen_account": staff_forms.ALREADY_DONE_MESSAGE})

    def test_refreshing_a_resend_does_not_replace_the_code_just_shown(self):
        self.page = household_page(account_row())
        number = self.number_in(self.client.get(RESEND))
        data = {**FORM, "form_number": number}
        self.assertIn("482 913", self.text(self.client.post(RESEND, data=data)))
        again = self.client.post(RESEND, data=data)
        self.assertEqual((again.status_code, again.location), (303, ASSESSMENT))
        self.assertEqual(self.mocks["resend"].call_count, 1)

    def test_a_double_click_acts_only_once(self):
        self.page = household_page(account_row())
        number = self.number_in(self.client.get(RESEND))
        statuses = [self.client.post(RESEND, data={**FORM, "form_number": number}).status_code
                    for _ in range(3)]
        self.assertEqual(statuses, [200, 303, 303])
        self.assertEqual(self.mocks["resend"].call_count, 1)

    def test_posts_without_a_known_number_change_nothing(self):
        for data in (FORM, {**FORM, "form_number": ""}, {**FORM, "form_number": "guessed"}):
            for path in (CREATE, RESEND):
                with self.subTest(path=path, number=data.get("form_number")):
                    response = self.client.post(path, data=data)
                    self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.mocks["create"].assert_not_called()
        self.mocks["resend"].assert_not_called()

    def test_a_number_from_another_browser_session_is_not_accepted(self):
        number = self.number_in(self.client.get(CREATE))
        other = self.app.test_client()
        with other.session_transaction() as session:
            session.update(user_id=5, session_version=1, issued_at=int(time()),
                           csrf_token="synthetic-csrf")
        response = other.post(CREATE, data={**FORM, "form_number": number})
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.mocks["create"].assert_not_called()

    def test_a_form_with_mistakes_gets_a_fresh_number_so_the_correction_can_be_sent(self):
        errors = {"verification_note": "Write a short note of 10 to 500 characters."}
        self.mocks["create"].side_effect = [CitizenAccountInputError(errors), self.sent_result]
        number = self.number_in(self.client.get(CREATE))
        rejected = self.client.post(CREATE, data={**FORM, "form_number": number})
        self.assertEqual(rejected.status_code, 400)
        new_number = self.number_in(rejected)
        self.assertNotEqual(new_number, number)
        accepted = self.client.post(CREATE, data={**FORM, "form_number": new_number})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.mocks["create"].call_count, 2)

    def test_the_repeat_check_comes_after_the_csrf_check(self):
        response = self.client.post(CREATE, data={**FORM, "csrf_token": "wrong"})
        self.assertEqual(response.status_code, 400)


class AssessmentPanelTests(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.source = detail_source()
        for target in (details, review_service):
            patcher = patch.object(target, "get_active_assessment_detail", return_value=self.source)
            patcher.start()
            self.addCleanup(patcher.stop)

    def panel(self, row, role="lgu_staff", query=""):
        self.source["citizen_account"] = row
        self.sign_in(role)
        return self.text(self.client.get(ASSESSMENT + query))

    def test_no_account_offers_create_to_lgu_staff_only(self):
        staff = self.panel(None)
        self.assertIn("Citizen Account", staff)
        self.assertIn("Create an account to give this household's representative access to the Citizen Portal.", staff)
        self.assertIn(f'href="{CREATE}"', staff)
        admin = self.panel(None, "system_administrator")
        self.assertIn("Create an account to give this household's representative access to the Citizen Portal.", admin)
        self.assertNotIn(CREATE, admin)

    def test_pending_account_shows_status_email_state_and_resend(self):
        text = self.panel(account_row())
        self.assertIn("Pending Activation", text)
        self.assertIn("Synthetic Citizen", text)
        self.assertIn("citizen@example.com", text)
        self.assertIn("Activation email sent on", text)
        self.assertIn("The activation link and code are valid until", text)
        self.assertIn(f'href="{RESEND}"', text)
        self.assertNotIn(CREATE, text)
        self.assertNotIn(RESEND, self.panel(account_row(), "system_administrator"))

    def test_pending_problems_are_visible_to_staff(self):
        text = self.panel(account_row(email_accepted_at=None, link_valid=False, expires_at=NOW))
        self.assertIn("The activation email was not sent.", text)
        self.assertIn("have expired", text)
        self.assertIn(RESEND, text)

    def test_active_and_deactivated_accounts_have_no_resend_or_create(self):
        for status, label in (("active", "Active"), ("deactivated", "Deactivated")):
            with self.subTest(status=status):
                text = self.panel(account_row(status))
                self.assertIn(label, text)
                self.assertIn("citizen@example.com", text)
                self.assertNotIn(RESEND, text)
                self.assertNotIn(CREATE, text)
        self.assertIn("Activated", self.panel(account_row("active")))

    def test_unavailable_account_information_does_not_break_the_page(self):
        for row in ("unavailable", ):
            text = self.panel(row)
            self.assertIn("Citizen Account information is unavailable right now.", text)
            self.assertNotIn(CREATE, text)
        self.source.pop("citizen_account")
        self.sign_in()
        response = self.client.get(ASSESSMENT)
        self.assertEqual(response.status_code, 200)
        self.assertIn("unavailable right now", self.text(response))

    def test_the_section_belongs_to_the_overview_and_never_shows_credentials(self):
        text = self.panel(account_row())
        section = re.search(r'<section class="citizen-account-panel".*?</section>', text, re.S).group(0)
        for word in ("activation code:", "password", "/portal/", "token", "http"):
            self.assertNotIn(word, section.lower())
        for query in ("?tab=mpi", "?tab=screening", "?tab=reviews"):
            with self.subTest(query=query):
                self.assertNotIn('class="citizen-account-panel"',
                                 self.panel(account_row(), query=query))

    def test_creating_an_account_does_not_start_a_staff_review(self):
        # The create form explains this; the panel only links to it.
        self.sign_in()
        self.assertIn("Creating an account does not start a Staff Review",
                      self.text(self.client.get(CREATE)))
        self.assertNotIn("reviews/", CREATE)


if __name__ == "__main__":
    unittest.main()
