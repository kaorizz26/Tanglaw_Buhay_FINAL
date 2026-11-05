"""Staff document requests (Task 3B): service rules on an isolated schema, then the pages.

Service tests use a temporary schema in the test database with synthetic data. Route
tests mock the services. The development database is never touched and no email is sent.
"""

from datetime import datetime, timedelta, timezone
import re
import unittest
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from repositories import document_requests as repository
from routes import document_requests as routes
from services import assessment_details as details
from services import assessment_queries
from services import document_requests as service
from services import staff_reviews as review_service
from services.assessment_queries import AssessmentQueryError
from services.citizen_account_panel import PANEL_UNAVAILABLE, present_account_panel
from services.citizen_accounts import CitizenAccountError
from services.document_request_panel import present_document_requests, present_request_history

import test_citizen_account_creation as creation
from test_citizen_account_creation import HOUSEHOLD, TEST_URL
from test_citizen_account_routes import (
    ASSESSMENT, FORM, RouteTestCase, account_row, household_page,
)

NOW = datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc)
REASON = "The household already submitted this document."
NEW_URL = f"/document-requests/household/{HOUSEHOLD_UUID}/new"


def request_form(*documents):
    """Build a posted form from (title, instructions) pairs."""
    form = MultiDict()
    for title, instructions in documents:
        form.add("document_title", title)
        form.add("document_instructions", instructions)
    return form


class FormValidationTests(unittest.TestCase):
    def errors(self, *documents):
        with self.assertRaises(service.DocumentRequestInputError) as caught:
            service.validate_request_form(request_form(*documents))
        return caught.exception.errors

    def test_each_row_keeps_a_cleaned_title_and_optional_instructions(self):
        documents = service.validate_request_form(request_form(
            ("  Valid   ID ", "  Front and back.  "), ("Proof of income", "   "),
        ))
        self.assertEqual(documents, [
            {"title": "Valid ID", "instructions": "Front and back."},
            {"title": "Proof of income", "instructions": None},
        ])

    def test_empty_rows_are_ignored_wherever_they_are(self):
        documents = service.validate_request_form(request_form(
            ("", ""), ("Valid ID", ""), ("  ", " "), ("Birth certificate", ""), ("", ""),
        ))
        self.assertEqual([item["title"] for item in documents], ["Valid ID", "Birth certificate"])

    def test_at_least_one_document_is_needed(self):
        for documents in ((), (("", ""), ("  ", ""))):
            self.assertEqual(set(self.errors(*documents)), {"form"})

    def test_a_row_problem_is_reported_against_that_row_only(self):
        errors = self.errors(("Valid ID", ""), ("ab", ""), ("", "Instructions with no name"),
                             ("Good title", "x" * 501), ("Bad\x00title", ""))
        self.assertEqual(set(errors), {"title_1", "title_2", "instructions_3", "title_4"})
        for message in errors.values():
            self.assertTrue(message.endswith("."))

    def test_titles_have_a_length_limit_and_cannot_repeat(self):
        self.assertEqual(set(self.errors(("x" * 121, ""))), {"title_0"})
        self.assertEqual(set(self.errors(("Valid ID", ""), ("valid  id", ""))), {"title_1"})
        self.assertEqual(len(service.validate_request_form(request_form(("x" * 120, "y" * 500)))), 1)

    def test_a_form_may_hold_at_most_ten_documents(self):
        ten = [(f"Document {number}", "") for number in range(10)]
        self.assertEqual(len(service.validate_request_form(request_form(*ten))), 10)
        self.assertEqual(set(self.errors(*ten, ("One more", ""))), {"form"})

    def test_the_cancellation_reason_needs_ten_to_five_hundred_characters(self):
        self.assertEqual(service.validate_cancellation_form({"reason_note": f" {REASON} "}), REASON)
        for reason in ("", "short", "x" * 501, "ok" * 5 + "\x00"):
            with self.assertRaises(service.DocumentRequestInputError) as caught:
                service.validate_cancellation_form({"reason_note": reason})
            self.assertEqual(set(caught.exception.errors), {"reason_note"})


class PresenterTests(unittest.TestCase):
    ROW = {"id": 4, "title": "Valid ID", "instructions": None, "status": "requested",
           "created_at": NOW, "updated_at": NOW}

    def test_each_status_has_a_plain_label_a_next_step_and_a_cancel_rule(self):
        expected = {"requested": ("Waiting for upload", True), "submitted": ("Ready for review", True),
                    "needs_correction": ("Needs correction", True), "accepted": ("Accepted", False),
                    "cancelled": ("Cancelled", False)}
        for status, (label, can_cancel) in expected.items():
            shown = present_document_requests([{**self.ROW, "status": status}])["requests"][0]
            self.assertEqual((shown["status_label"], shown["can_cancel"]), (label, can_cancel))
            self.assertTrue(shown["next_step"].endswith("."))

    def test_unavailable_and_empty_lists_are_described(self):
        self.assertEqual(present_document_requests(PANEL_UNAVAILABLE)["state"], "unavailable")
        self.assertEqual(present_document_requests([]), {"state": "ready", "requests": []})

    def test_the_history_is_newest_first_and_names_who_acted(self):
        events = [
            {"action": "requested", "staff_name": "Synthetic Staff", "citizen_name": None,
             "created_at": NOW, "note": None},
            {"action": "cancelled", "staff_name": "Synthetic Staff", "citizen_name": None,
             "created_at": NOW, "note": REASON},
        ]
        shown = present_request_history(events)
        self.assertEqual([item["action_label"] for item in shown], ["Request cancelled", "Document requested"])
        self.assertEqual(shown[0]["note"], REASON)


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic document tests.")
class StaffDocumentRequestTests(unittest.TestCase):
    # Reuse the isolated-schema fixture from the account creation tests without re-running them.
    _creation_setup = creation.AccountCreationTests.setUp
    _drop_schema = creation.AccountCreationTests._drop_schema
    _connect = creation.AccountCreationTests._connect
    _seed = creation.AccountCreationTests._seed
    _record_send = creation.AccountCreationTests._record_send
    error_code = creation.AccountCreationTests.error_code
    detail = creation.AccountCreationTests.detail

    def setUp(self):
        self._creation_setup()
        creation.AccountCreationTests.create(self)
        self.account_id = self.set_account_status("active")

    # ---- helpers ----------------------------------------------------------

    def set_account_status(self, status):
        """Move the household's account to a status, with the fields the database requires."""
        password = "'scrypt:x$y$z'" if status != "pending_activation" else "NULL"
        activated = "now()" if status == "active" else "NULL"
        return self.connection.execute(
            f"UPDATE citizen_accounts SET status = %s, password_hash = {password},"
            f" activated_at = {activated} RETURNING id", (status,)).fetchone()[0]

    def request_documents(self, *documents, staff=None, run=None, batch=None):
        return service.create_document_requests(
            staff or self.staff_id, HOUSEHOLD, batch or self.batch, run or self.run,
            request_form(*(documents or (("Valid ID", ""),))), connection_factory=self._connect,
        )

    def cancel(self, request_id, reason=REASON, staff=None, batch=None):
        return service.cancel_document_request(
            staff or self.staff_id, request_id, batch or self.batch, self.run,
            {"reason_note": reason}, connection_factory=self._connect,
        )

    def requests(self):
        return self.connection.execute(
            "SELECT id, title, instructions, status, requested_by FROM document_requests ORDER BY id"
        ).fetchall()

    def events(self):
        return self.connection.execute(
            "SELECT request_id, action, staff_id, note FROM document_request_events ORDER BY id"
        ).fetchall()

    def advance(self, request_id, *statuses):
        with self._connect() as connection:
            for status in statuses:
                repository.change_status(connection, request_id, status)

    # ---- creating requests ------------------------------------------------

    def test_several_documents_are_saved_together_as_separate_requests(self):
        count = self.request_documents(("Valid ID", "Front and back."), ("Proof of income", ""),
                                       ("Barangay certificate", ""))
        self.assertEqual(count, 3)
        rows = self.requests()
        self.assertEqual([row[1] for row in rows], ["Valid ID", "Proof of income", "Barangay certificate"])
        self.assertEqual([row[2] for row in rows], ["Front and back.", None, None])
        self.assertEqual({(row[3], row[4]) for row in rows}, {("requested", self.staff_id)})
        self.assertEqual(len({row[0] for row in rows}), 3)

    def test_each_request_records_who_asked_in_its_own_history(self):
        self.request_documents(("Valid ID", ""), ("Proof of income", ""))
        ids = [row[0] for row in self.requests()]
        self.assertEqual(self.events(), [(ids[0], "requested", self.staff_id, None),
                                         (ids[1], "requested", self.staff_id, None)])

    def test_a_failure_part_way_saves_none_of_the_documents(self):
        real_insert = repository.insert_event
        calls = []

        def fail_on_second(*arguments, **keywords):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("history write failed")
            return real_insert(*arguments, **keywords)

        with patch.object(repository, "insert_event", side_effect=fail_on_second):
            self.assertEqual(self.error_code(self.request_documents, ("One", ""), ("Two", ""),
                                             ("Three", "")), "unavailable")
        self.assertEqual((self.requests(), self.events()), ([], []))

    def test_a_form_mistake_saves_nothing(self):
        with self.assertRaises(service.DocumentRequestInputError):
            self.request_documents(("Valid ID", ""), ("ab", ""))
        self.assertEqual(self.requests(), [])

    def test_only_an_active_account_can_be_asked_for_documents(self):
        for status in ("pending_activation", "deactivated"):
            with self.subTest(status=status):
                self.set_account_status(status)
                self.assertEqual(self.error_code(self.request_documents), "account_not_active")
        self.assertEqual(self.requests(), [])

    def test_a_household_without_an_account_cannot_be_asked(self):
        self.connection.execute("DELETE FROM citizen_account_tokens")
        self.connection.execute("ALTER TABLE citizen_account_events DISABLE TRIGGER citizen_account_events_immutable")
        self.connection.execute("DELETE FROM citizen_account_events")
        self.connection.execute("DELETE FROM citizen_accounts")
        self.assertEqual(self.error_code(self.request_documents), "account_missing")

    def test_only_active_lgu_staff_may_request_documents(self):
        for actor in (self.admin_id, self.inactive_id):
            with self.subTest(actor=actor):
                self.assertEqual(self.error_code(self.request_documents, staff=actor), "unauthorized")
        self.assertEqual(self.requests(), [])

    def test_the_selected_survey_must_still_match(self):
        self.assertEqual(self.error_code(self.request_documents, run=self.run + 1), "context_changed")
        self.assertEqual(self.error_code(self.request_documents, staff=-1), "invalid_request")
        self.assertEqual(self.requests(), [])

    def test_a_second_form_adds_more_requests_without_touching_earlier_ones(self):
        self.request_documents(("Valid ID", ""))
        first_id = self.requests()[0][0]
        self.advance(first_id, "submitted")
        self.request_documents(("Proof of income", ""))
        self.assertEqual([(row[1], row[3]) for row in self.requests()],
                         [("Valid ID", "submitted"), ("Proof of income", "requested")])

    # ---- viewing and cancelling -------------------------------------------

    def test_the_request_page_shows_the_household_status_and_history(self):
        self.request_documents(("Valid ID", "Front and back."))
        request_id = self.requests()[0][0]
        page = service.get_document_request_page(request_id, connection_factory=self._connect)
        self.assertEqual((page["request"]["title"], page["request"]["status_label"]),
                         ("Valid ID", "Waiting for upload"))
        self.assertEqual((page["household_uuid"], page["reference_id"]), (HOUSEHOLD, "HH-00001"))
        self.assertEqual(page["representative_name"], "Synthetic Citizen")
        self.assertEqual([item["action_label"] for item in page["history"]], ["Document requested"])
        self.assertEqual(page["history"][0]["actor_name"], "synthetic_staff")

    def test_an_unknown_request_is_not_found(self):
        with self.assertRaises(AssessmentQueryError) as caught:
            service.get_document_request_page(999999, connection_factory=self._connect)
        self.assertEqual(str(caught.exception), "document_request_missing")

    def test_cancelling_records_the_reason_and_the_staff_member(self):
        self.request_documents(("Valid ID", ""), ("Proof of income", ""))
        first_id = self.requests()[0][0]
        self.cancel(first_id)
        self.assertEqual([row[3] for row in self.requests()], ["cancelled", "requested"])
        self.assertEqual(self.events()[-1], (first_id, "cancelled", self.staff_id, REASON))

    def test_every_open_status_can_be_cancelled(self):
        for steps in ((), ("submitted",), ("submitted", "needs_correction")):
            with self.subTest(steps=steps):
                self.request_documents((f"Document {len(steps)}", ""))
                request_id = self.requests()[-1][0]
                self.advance(request_id, *steps)
                self.cancel(request_id)
                self.assertEqual(self.requests()[-1][3], "cancelled")

    def test_accepted_and_cancelled_requests_are_final(self):
        self.request_documents(("Accepted one", ""), ("Cancelled one", ""))
        accepted_id, cancelled_id = [row[0] for row in self.requests()]
        self.advance(accepted_id, "submitted", "accepted")
        self.cancel(cancelled_id)
        events_before = len(self.events())
        for request_id in (accepted_id, cancelled_id):
            self.assertEqual(self.error_code(self.cancel, request_id), "not_cancellable")
        self.assertEqual(len(self.events()), events_before)
        self.assertEqual([row[3] for row in self.requests()], ["accepted", "cancelled"])

    def test_a_cancellation_needs_a_reason_a_known_request_and_an_active_lgu_staff_member(self):
        self.request_documents()
        request_id = self.requests()[0][0]
        with self.assertRaises(service.DocumentRequestInputError):
            self.cancel(request_id, reason="short")
        self.assertEqual(self.error_code(self.cancel, 999999), "request_missing")
        self.assertEqual(self.error_code(self.cancel, request_id, batch=self.batch + 1), "request_missing")
        for actor in (self.admin_id, self.inactive_id):
            self.assertEqual(self.error_code(self.cancel, request_id, staff=actor), "unauthorized")
        self.assertEqual(self.requests()[0][3], "requested")

    # ---- the Assessment page ---------------------------------------------

    def test_the_assessment_snapshot_lists_the_households_requests(self):
        self.request_documents(("Valid ID", ""), ("Proof of income", ""))
        snapshot = self.detail()
        self.assertEqual([row["title"] for row in snapshot["document_requests"]],
                         ["Valid ID", "Proof of income"])

    def test_missing_document_tables_never_break_the_assessment_snapshot(self):
        self.connection.execute(
            "DROP TABLE document_request_events, document_files, document_requests")
        snapshot = self.detail()
        self.assertEqual(snapshot["document_requests"], PANEL_UNAVAILABLE)
        self.assertEqual(snapshot["identity"]["source_row_number"], 1)
        self.assertEqual(snapshot["citizen_account"]["status"], "active")

    # ---- no effect on assessments ----------------------------------------

    def test_requesting_and_cancelling_change_no_assessment_or_review_rows(self):
        watched = ("household_assessments", "assessment_runs", "assessment_run_selections",
                   "staff_reviews", "households", "household_members")

        def counts():
            return {table: self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in watched}

        before = counts()
        self.request_documents(("Valid ID", ""), ("Proof of income", ""))
        self.cancel(self.requests()[0][0])
        self.assertEqual(counts(), before)


# ---- Pages --------------------------------------------------------------------

def request_row(status="requested", **changes):
    row = {"id": 8, "title": "Valid ID", "instructions": "Front and back.", "status": status,
           "created_at": NOW, "updated_at": NOW}
    row.update(changes)
    return row


def request_page(status="requested"):
    """What get_document_request_page returns, built from the same presenters."""
    return {
        **{key: value for key, value in household_page().items() if key != "account"},
        "request": present_document_requests([request_row(status)])["requests"][0],
        "representative_name": "Synthetic Citizen",
        "history": present_request_history([
            {"action": "requested", "staff_name": "Synthetic Staff", "citizen_name": None,
             "created_at": NOW, "note": None}]),
    }


class DocumentRouteCase(RouteTestCase):
    def setUp(self):
        super().setUp()
        self.page = household_page(account_row("active"))
        self.request_page = request_page()
        patches = {
            "household": patch.object(routes, "get_household_account_page",
                                      side_effect=lambda *args, **kw: self.page),
            "request_page": patch.object(routes, "get_document_request_page",
                                         side_effect=lambda *args, **kw: self.request_page),
            "create": patch.object(routes, "create_document_requests", return_value=2),
            "cancel": patch.object(routes, "cancel_document_request"),
        }
        for name, patcher in patches.items():
            self.mocks["documents_" + name] = patcher.start()
            self.addCleanup(patcher.stop)

    def open_form(self, path):
        """Open a form like a browser and return the one-time number it carries."""
        self.sign_in()
        page = self.client.get(path)
        return re.search(r'name="form_number" value="([^"]+)"', page.get_data(as_text=True)).group(1)

    def post_documents(self, **changes):
        number = self.open_form(NEW_URL)
        data = {**FORM, "form_number": number, "document_title": ["Valid ID", "Proof of income", ""],
                "document_instructions": ["Front and back.", "", ""], **changes}
        return self.client.post(NEW_URL, data=data)

    def cancel_url(self, request_id=8):
        return f"/document-requests/{request_id}/cancel"


class DocumentAuthorizationTests(DocumentRouteCase):
    PATHS = {NEW_URL: ("get", "post"), "/document-requests/8": ("get",),
             "/document-requests/8/cancel": ("get", "post")}

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        for path, methods in self.PATHS.items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, data=FORM)
                    self.assertEqual((response.status_code, response.location), (302, "/login"))
        self.mocks["documents_household"].assert_not_called()

    def test_administrators_are_refused(self):
        self.sign_in("system_administrator")
        for path, methods in self.PATHS.items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path, data=FORM).status_code, 403)
        for name in ("create", "cancel"):
            self.mocks["documents_" + name].assert_not_called()

    def test_the_request_page_only_reads(self):
        self.sign_in()
        self.assertEqual(self.client.get("/document-requests/8").status_code, 200)
        for method in ("post", "put", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)("/document-requests/8", data=FORM).status_code, 405)

    def test_every_post_needs_a_valid_csrf_token(self):
        for path in (NEW_URL, self.cancel_url()):
            with self.subTest(path=path):
                number = self.open_form(path)
                response = self.client.post(path, data={**FORM, "csrf_token": "forged",
                                                        "form_number": number, "reason_note": REASON})
                self.assertEqual(response.status_code, 400)
        self.mocks["documents_create"].assert_not_called()
        self.mocks["documents_cancel"].assert_not_called()


class RequestFormTests(DocumentRouteCase):
    def test_an_active_account_gets_a_form_with_empty_rows_for_each_document(self):
        self.sign_in()
        text = self.main_text(self.client.get(NEW_URL))
        self.assertEqual(text.count('name="document_title"'), 3)
        self.assertEqual(text.count('name="document_instructions"'), 3)
        for words in ("Request Documents", "Document 1", "Document 3", "(optional)",
                      "does not approve a subsidy or start a Staff Review"):
            self.assertIn(words, text)
        self.assertIn("HH-0008", text)

    def test_other_account_states_return_to_the_assessment_with_a_message(self):
        self.sign_in()
        for status in ("pending_activation", "deactivated"):
            self.page = household_page(account_row(status))
            response = self.client.get(NEW_URL)
            self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.page = household_page("none")
        self.assertEqual(self.client.get(NEW_URL).status_code, 303)
        self.page = {**household_page(), "account": {"state": "unavailable"}}
        self.assertEqual(self.client.get(NEW_URL).status_code, 503)

    def test_a_valid_form_saves_every_document_and_returns_to_the_assessment(self):
        response = self.post_documents()
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        arguments = self.mocks["documents_create"].call_args.args
        self.assertEqual(arguments[:4], (5, HOUSEHOLD_UUID, 3, 7))
        self.assertEqual(arguments[4].getlist("document_title"), ["Valid ID", "Proof of income", ""])
        with self.client.session_transaction() as session:
            self.assertEqual(dict(session["_flashes"])["document_requests"], "2 documents requested.")

    def test_one_document_is_reported_in_the_singular(self):
        self.mocks["documents_create"].return_value = 1
        self.post_documents()
        with self.client.session_transaction() as session:
            self.assertEqual(dict(session["_flashes"])["document_requests"], "1 document requested.")

    def test_mistakes_keep_what_was_typed_and_point_at_the_row(self):
        errors = {"title_1": "Enter a document name of 3 to 120 characters."}
        self.mocks["documents_create"].side_effect = service.DocumentRequestInputError(errors)
        response = self.post_documents(document_title=["Valid ID", "ab", ""])
        text = self.text(response)
        self.assertEqual(response.status_code, 400)
        self.assertIn(errors["title_1"], text)
        self.assertIn('value="Valid ID"', text)
        self.assertIn('value="ab"', text)
        self.assertIn("Front and back.", text)
        self.assertIn('aria-invalid="true"', text)

    def test_an_account_that_stopped_being_active_returns_to_the_assessment(self):
        self.mocks["documents_create"].side_effect = CitizenAccountError("account_not_active")
        response = self.post_documents()
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        with self.client.session_transaction() as session:
            self.assertIn("Active", dict(session["_flashes"])["document_requests"])

    def test_unexpected_failures_stop_with_plain_pages(self):
        for code, status in (("unauthorized", 403), ("context_changed", 400), ("unavailable", 503)):
            with self.subTest(code=code):
                self.mocks["documents_create"].side_effect = CitizenAccountError(code)
                self.assertEqual(self.post_documents().status_code, status)

    def test_a_refresh_or_double_click_saves_the_documents_only_once(self):
        number = self.open_form(NEW_URL)
        data = {**FORM, "form_number": number, "document_title": ["Valid ID"],
                "document_instructions": [""]}
        statuses = [self.client.post(NEW_URL, data=data).status_code for _ in range(3)]
        self.assertEqual(statuses, [303, 303, 303])
        self.assertEqual(self.mocks["documents_create"].call_count, 1)
        with self.client.session_transaction() as session:
            message = dict(session["_flashes"])["document_requests"]
        self.assertEqual(message, routes.ALREADY_DONE_MESSAGE)
        self.assertNotIn("Activation", message)


class RequestPageTests(DocumentRouteCase):
    def test_the_page_shows_the_document_its_status_what_to_do_and_its_history(self):
        self.sign_in()
        text = self.main_text(self.client.get("/document-requests/8"))
        for words in ("Valid ID", "Waiting for upload", "Waiting for the representative to upload",
                      "Front and back.", "Synthetic Citizen", "HH-0008", "Document requested",
                      "By Synthetic Staff", "Cancel Request"):
            self.assertIn(words, text)
        self.assertIn('href="/document-requests/8/cancel"', text)

    def test_final_requests_offer_no_cancel_button(self):
        self.sign_in()
        for status in ("accepted", "cancelled"):
            self.request_page = request_page(status)
            self.assertNotIn("/cancel", self.main_text(self.client.get("/document-requests/8")))

    def test_a_missing_request_is_a_plain_not_found_page(self):
        self.mocks["documents_request_page"].side_effect = AssessmentQueryError("document_request_missing")
        self.sign_in()
        self.assertEqual(self.client.get("/document-requests/99").status_code, 404)
        self.mocks["documents_request_page"].side_effect = AssessmentQueryError("database_unavailable")
        self.assertEqual(self.client.get("/document-requests/99").status_code, 503)


class CancelPageTests(DocumentRouteCase):
    def test_the_page_explains_the_consequences_and_asks_for_a_reason(self):
        self.sign_in()
        response = self.client.get(self.cancel_url())
        text = self.main_text(response)
        for words in ("Cancel this request?", "no longer asked for this document",
                      "shown to the representative", "cannot be reopened",
                      "Why is this request being cancelled?", "Keep Request"):
            self.assertIn(words, text)
        self.assertIn("account-button-danger", self.text(response))

    def test_final_requests_cannot_be_cancelled(self):
        number = self.open_form(self.cancel_url())  # Opened while the request was still open.
        self.request_page = request_page("accepted")
        self.assertEqual(self.client.get(self.cancel_url()).location, ASSESSMENT)
        response = self.client.post(self.cancel_url(), data={**FORM, "form_number": number,
                                                              "reason_note": REASON})
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.mocks["documents_cancel"].assert_not_called()

    def test_a_valid_cancellation_returns_to_the_assessment_with_a_message(self):
        number = self.open_form(self.cancel_url())
        response = self.client.post(self.cancel_url(), data={**FORM, "form_number": number,
                                                              "reason_note": REASON})
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))
        self.assertEqual(self.mocks["documents_cancel"].call_args.args[:4], (5, 8, 3, 7))
        with self.client.session_transaction() as session:
            self.assertIn("cancelled", dict(session["_flashes"])["document_requests"])

    def test_a_missing_reason_shows_the_form_again(self):
        errors = {"reason_note": "Write a reason of 10 to 500 characters."}
        self.mocks["documents_cancel"].side_effect = service.DocumentRequestInputError(errors)
        number = self.open_form(self.cancel_url())
        response = self.client.post(self.cancel_url(), data={**FORM, "form_number": number,
                                                              "reason_note": "no"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(errors["reason_note"], self.text(response))
        self.assertIn(">no</textarea>", self.text(response))

    def test_a_request_that_changed_meanwhile_returns_to_the_assessment(self):
        self.mocks["documents_cancel"].side_effect = CitizenAccountError("not_cancellable")
        number = self.open_form(self.cancel_url())
        response = self.client.post(self.cancel_url(), data={**FORM, "form_number": number,
                                                              "reason_note": REASON})
        self.assertEqual((response.status_code, response.location), (303, ASSESSMENT))

    def test_a_double_click_cancels_only_once(self):
        number = self.open_form(self.cancel_url())
        data = {**FORM, "form_number": number, "reason_note": REASON}
        for _ in range(3):
            self.client.post(self.cancel_url(), data=data)
        self.assertEqual(self.mocks["documents_cancel"].call_count, 1)


class AssessmentSectionTests(DocumentRouteCase):
    def setUp(self):
        super().setUp()
        self.source = detail_source()
        for target in (details, review_service):
            patcher = patch.object(target, "get_active_assessment_detail", return_value=self.source)
            patcher.start()
            self.addCleanup(patcher.stop)

    def section(self, status="active", requests=(), role="lgu_staff"):
        self.source["citizen_account"] = account_row(status)
        self.source["document_requests"] = list(requests)
        self.sign_in(role)
        text = self.text(self.client.get(ASSESSMENT))
        found = re.search(r'<section class="citizen-account-panel" aria-labelledby="document-requests-heading">.*?</section>', text, re.S)
        return found.group(0) if found else ""

    def test_an_active_account_lists_each_document_with_its_status_and_a_details_link(self):
        rows = [request_row("requested", id=8), request_row("submitted", id=9, title="Proof of income"),
                request_row("accepted", id=10, title="Birth certificate")]
        text = self.section(requests=rows)
        for words in ("Document Requests", "Valid ID", "Waiting for upload", "Proof of income",
                      "Ready for review", "Birth certificate", "Accepted",
                      "The representative uploaded a file. It is ready for your review."):
            self.assertIn(words, text)
        for request_id in (8, 9, 10):
            self.assertIn(f'href="/document-requests/{request_id}"', text)
        self.assertIn(f'href="{NEW_URL}"', text)

    def test_an_active_account_without_requests_invites_the_first_one(self):
        text = self.section()
        self.assertIn("No documents have been requested yet.", text)
        self.assertIn("Request Documents", text)

    def test_other_account_states_cannot_request_documents(self):
        for status in ("pending_activation", "deactivated"):
            text = self.section(status, [request_row()])
            self.assertIn("Documents can be requested while the Citizen Account is Active.", text)
            self.assertNotIn(NEW_URL, text)
            self.assertIn("Valid ID", text)

    def test_administrators_and_households_without_an_account_see_no_section(self):
        self.assertEqual(self.section(requests=[request_row()], role="system_administrator"), "")
        self.source["citizen_account"] = None
        self.sign_in()
        self.assertNotIn("document-requests-heading", self.text(self.client.get(ASSESSMENT)))

    def test_unavailable_document_requests_do_not_break_the_page(self):
        self.source["citizen_account"] = account_row("active")
        self.source["document_requests"] = PANEL_UNAVAILABLE
        self.sign_in()
        response = self.client.get(ASSESSMENT)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Document requests are unavailable right now.", self.text(response))

    def test_the_section_never_shows_files_paths_or_internal_names(self):
        text = self.section(requests=[request_row()]).lower()
        for word in ("storage", "sha256", "requested_by", "password", "token", "/portal/"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
