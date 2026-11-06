"""Staff document review (Task 3D): downloads, Accept and Needs Correction.

Service tests use an isolated schema in the test database and a temporary private
storage folder, with synthetic data. Route tests mock the services. The development
database is never touched and no email is sent.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from repositories import document_requests as repository
from routes import document_requests as routes
from services import document_requests as request_service
from services import document_reviews as service
from services.citizen_accounts import CitizenAccountError
from services.document_request_panel import present_files
from services.document_requests import DocumentRequestInputError

import test_citizen_account_creation as creation
import test_document_requests_staff as staff_tests
from test_citizen_account_routes import FORM
from test_document_requests_staff import NOW, DocumentRouteCase, request_page

PDF = b"%PDF-1.7\nsynthetic document\n%%EOF"
REASON = "The photo is blurry. Please upload a clear photo of the front and back."


class ReviewServiceTests(unittest.TestCase):
    """Reuses the isolated-schema fixture and helpers of the Task 3B service tests.

    The helpers are borrowed one by one, so the Task 3B tests themselves do not run again.
    """

    _creation_setup = creation.AccountCreationTests.setUp
    _drop_schema = creation.AccountCreationTests._drop_schema
    _connect = creation.AccountCreationTests._connect
    _seed = creation.AccountCreationTests._seed
    _record_send = creation.AccountCreationTests._record_send
    error_code = creation.AccountCreationTests.error_code
    detail = creation.AccountCreationTests.detail
    _staff_setup = staff_tests.StaffDocumentRequestTests.setUp
    set_account_status = staff_tests.StaffDocumentRequestTests.set_account_status
    request_documents = staff_tests.StaffDocumentRequestTests.request_documents
    cancel = staff_tests.StaffDocumentRequestTests.cancel
    requests = staff_tests.StaffDocumentRequestTests.requests
    events = staff_tests.StaffDocumentRequestTests.events

    def setUp(self):
        self._staff_setup()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.storage = Path(folder.name).resolve()
        app = Flask(__name__)
        app.config["DOCUMENT_STORAGE_DIR"] = str(self.storage)
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)

    # ---- helpers ----------------------------------------------------------

    def upload(self, request_id, version=1, data=PDF):
        """Do what a citizen's upload does: store a file, record it, and wait for review."""
        key = f"{secrets.token_hex(16)}.pdf"
        (self.storage / key).write_bytes(data)
        with self._connect() as connection:
            record = repository.insert_file(
                connection, request_id, version, f"scan{version}.pdf", "application/pdf",
                len(data), "a" * 64, key)
            repository.change_status(connection, request_id, "submitted")
            repository.insert_event(
                connection, request_id, "uploaded", account_id=self.account_id,
                file_id=record["id"])
        return record

    def submitted_request(self, title="Valid ID"):
        self.request_documents((title, ""))
        request_id = self.requests()[-1][0]
        return request_id, self.upload(request_id)

    def accept(self, request_id, file_id, staff=None, batch=None, run=None):
        service.accept_document(
            staff or self.staff_id, request_id, batch or self.batch, run or self.run, file_id,
            connection_factory=self._connect)

    def correct(self, request_id, file_id, reason=REASON, staff=None):
        service.request_correction(
            staff or self.staff_id, request_id, self.batch, self.run, file_id,
            {"reason_note": reason}, connection_factory=self._connect)

    def status_of(self, request_id):
        return self.connection.execute(
            "SELECT status FROM document_requests WHERE id = %s", (request_id,)).fetchone()[0]

    def code_of(self, call, *arguments, **keywords):
        with self.assertRaises(CitizenAccountError) as caught:
            call(*arguments, **keywords)
        return str(caught.exception)

    # ---- Accept -----------------------------------------------------------

    def test_accepting_records_the_file_and_the_staff_member(self):
        request_id, record = self.submitted_request()
        self.accept(request_id, record["id"])
        self.assertEqual(self.status_of(request_id), "accepted")
        action = self.connection.execute(
            "SELECT action, staff_id, account_id, file_id, note FROM document_request_events"
            " WHERE request_id = %s ORDER BY id DESC LIMIT 1", (request_id,)).fetchone()
        self.assertEqual(action, ("accepted", self.staff_id, None, record["id"], None))

    def test_an_accepted_request_is_final(self):
        request_id, record = self.submitted_request()
        self.accept(request_id, record["id"])
        self.assertEqual(self.code_of(self.accept, request_id, record["id"]), "not_reviewable")
        self.assertEqual(self.code_of(self.correct, request_id, record["id"]), "not_reviewable")
        self.assertEqual(self.code_of(self.cancel, request_id), "not_cancellable")
        self.assertEqual(self.status_of(request_id), "accepted")

    def test_only_a_request_ready_for_review_can_be_decided(self):
        self.request_documents(("Not uploaded", ""), ("Cancelled", ""), ("Fix it", ""))
        waiting, cancelled, to_fix = [row[0] for row in self.requests()]
        self.cancel(cancelled)
        record = self.upload(to_fix)
        self.correct(to_fix, record["id"])
        for request_id in (waiting, cancelled, to_fix):
            with self.subTest(request_id=request_id):
                before = len(self.events())
                self.assertEqual(
                    self.code_of(self.accept, request_id, record["id"]), "not_reviewable")
                self.assertEqual(
                    self.code_of(self.correct, request_id, record["id"]), "not_reviewable")
                self.assertEqual(len(self.events()), before)

    def test_an_older_file_cannot_be_accepted_when_a_newer_one_exists(self):
        request_id, first = self.submitted_request()
        self.correct(request_id, first["id"])
        second = self.upload(request_id, version=2)
        self.assertEqual(self.code_of(self.accept, request_id, first["id"]), "file_changed")
        self.assertEqual(self.status_of(request_id), "submitted")
        self.accept(request_id, second["id"])
        self.assertEqual(self.status_of(request_id), "accepted")

    def test_a_file_of_another_request_cannot_be_used(self):
        request_id, _ = self.submitted_request("Valid ID")
        _, other = self.submitted_request("Proof of income")
        self.assertEqual(self.code_of(self.accept, request_id, other["id"]), "file_changed")
        self.assertEqual(self.status_of(request_id), "submitted")

    # ---- Needs Correction -------------------------------------------------

    def test_a_correction_records_the_reason_the_citizen_will_read(self):
        request_id, record = self.submitted_request()
        self.correct(request_id, record["id"])
        self.assertEqual(self.status_of(request_id), "needs_correction")
        last = self.events()[-1]
        self.assertEqual(last, (request_id, "correction_requested", self.staff_id, REASON))

    def test_a_correction_needs_a_clear_reason(self):
        request_id, record = self.submitted_request()
        for reason in ("", "   ", "Blurry", "x" * 501):
            with self.subTest(reason=reason[:10]):
                with self.assertRaises(DocumentRequestInputError):
                    self.correct(request_id, record["id"], reason)
        self.assertEqual(self.status_of(request_id), "submitted")

    def test_the_whole_cycle_keeps_every_file_and_history_entry(self):
        request_id, first = self.submitted_request()
        self.correct(request_id, first["id"])
        second = self.upload(request_id, version=2)
        self.accept(request_id, second["id"])
        actions = [row[1] for row in self.events() if row[0] == request_id]
        self.assertEqual(actions, ["requested", "uploaded", "correction_requested",
                                   "uploaded", "accepted"])
        with self._connect() as connection:
            versions = [item["version_number"] for item in repository.list_files(connection, request_id)]
        self.assertEqual(versions, [2, 1])
        self.assertEqual(len(list(self.storage.iterdir())), 2)

    # ---- Who may decide ---------------------------------------------------

    def test_administrators_and_inactive_staff_are_refused(self):
        request_id, record = self.submitted_request()
        for staff in (self.admin_id, self.inactive_id):
            with self.subTest(staff=staff):
                self.assertEqual(
                    self.code_of(self.accept, request_id, record["id"], staff=staff), "unauthorized")
                self.assertEqual(
                    self.code_of(self.correct, request_id, record["id"], staff=staff), "unauthorized")
        self.assertEqual(self.status_of(request_id), "submitted")

    def test_a_changed_survey_selection_or_unknown_request_is_refused(self):
        request_id, record = self.submitted_request()
        self.assertEqual(
            self.code_of(self.accept, request_id, record["id"], run=self.run + 100), "context_changed")
        self.assertEqual(
            self.code_of(self.accept, request_id, record["id"], batch=self.batch + 100), "request_missing")
        self.assertEqual(self.code_of(self.accept, 999999, record["id"]), "request_missing")
        self.assertEqual(self.status_of(request_id), "submitted")

    def test_malformed_identifiers_are_rejected_before_any_work(self):
        for arguments in ((0, 1, 1, 1, 1), (1, "1", 1, 1, 1), (1, 1, 1, 1, None), (1, 1, 1, 1, 2**70)):
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    self.code_of(service.accept_document, *arguments,
                                 connection_factory=self._connect), "invalid_request")

    def test_a_decision_and_a_cancellation_at_once_leave_exactly_one_result(self):
        request_id, record = self.submitted_request()

        def attempt(kind):
            try:
                if kind == "accept":
                    self.accept(request_id, record["id"])
                else:
                    self.cancel(request_id)
                return "done"
            except CitizenAccountError as refused:
                return str(refused)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(attempt, ("accept", "cancel")))
        self.assertEqual(results[0], "done")
        self.assertIn(results[1], ("not_reviewable", "not_cancellable"))
        final = [row[1] for row in self.events() if row[0] == request_id][-1]
        self.assertEqual(self.status_of(request_id), {"accepted": "accepted", "cancelled": "cancelled"}[final])

    # ---- Downloads --------------------------------------------------------

    def open_file(self, request_id, file_id, staff=None):
        return service.open_file_for_staff(
            staff or self.staff_id, request_id, file_id, connection_factory=self._connect)

    def test_staff_can_open_an_uploaded_file(self):
        request_id, record = self.submitted_request()
        opened = self.open_file(request_id, record["id"])
        self.assertEqual(opened["path"].read_bytes(), PDF)
        self.assertEqual((opened["display_name"], opened["content_type"]),
                         ("scan1.pdf", "application/pdf"))

    def test_a_download_needs_the_right_request_and_an_active_staff_member(self):
        request_id, record = self.submitted_request("Valid ID")
        other_id, _ = self.submitted_request("Proof of income")
        self.assertEqual(self.code_of(self.open_file, other_id, record["id"]), "request_missing")
        self.assertEqual(self.code_of(self.open_file, request_id, 999999), "request_missing")
        self.assertEqual(
            self.code_of(self.open_file, request_id, record["id"], staff=self.admin_id), "unauthorized")
        self.assertEqual(
            self.code_of(self.open_file, request_id, record["id"], staff=self.inactive_id), "unauthorized")

    def test_a_file_missing_from_storage_is_not_found(self):
        request_id, record = self.submitted_request()
        for item in self.storage.iterdir():
            item.unlink()
        self.assertEqual(self.code_of(self.open_file, request_id, record["id"]), "request_missing")

    # ---- The request page and unrelated data ------------------------------

    def test_the_request_page_lists_files_and_marks_the_latest(self):
        request_id, first = self.submitted_request()
        self.correct(request_id, first["id"])
        page = request_service.get_document_request_page(request_id, connection_factory=self._connect)
        self.assertEqual([(f["version_number"], f["is_latest"], f["awaiting_review"]) for f in page["files"]],
                         [(1, True, False)])
        second = self.upload(request_id, version=2)
        page = request_service.get_document_request_page(request_id, connection_factory=self._connect)
        self.assertEqual([(f["version_number"], f["awaiting_review"]) for f in page["files"]],
                         [(2, True), (1, False)])
        self.assertEqual(page["files"][0]["id"], second["id"])
        self.assertTrue(page["request"]["can_review"])

    def test_reviewing_changes_no_assessment_or_staff_review_rows(self):
        watched = ("household_assessments", "assessment_runs", "staff_reviews", "households")
        count = lambda: [self.connection.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in watched]
        before = count()
        request_id, record = self.submitted_request()
        self.correct(request_id, record["id"])
        second = self.upload(request_id, version=2)
        self.accept(request_id, second["id"])
        self.assertEqual(count(), before)


class PresenterTests(unittest.TestCase):
    def files(self):
        rows = [{"id": 9, "version_number": 2, "display_name": "b.pdf", "size_bytes": 1536 * 1024,
                 "uploaded_at": NOW},
                {"id": 4, "version_number": 1, "display_name": "a.pdf", "size_bytes": 300,
                 "uploaded_at": NOW}]
        return rows

    def test_only_the_newest_file_awaits_review_and_sizes_are_readable(self):
        shown = present_files(self.files(), "submitted")
        self.assertEqual([f["awaiting_review"] for f in shown], [True, False])
        self.assertEqual([f["size_label"] for f in shown], ["1.5 MB", "1 KB"])

    def test_nothing_awaits_review_unless_the_request_is_ready_for_review(self):
        for status in ("requested", "needs_correction", "accepted", "cancelled"):
            self.assertFalse(any(f["awaiting_review"] for f in present_files(self.files(), status)))


# ---- Pages --------------------------------------------------------------------

def review_page(status="submitted"):
    """A request page with two uploaded versions, built with the real presenters."""
    page = request_page(status)
    files = [{"id": 9, "version_number": 2, "display_name": "second-scan.pdf",
              "size_bytes": 2048, "uploaded_at": NOW},
             {"id": 4, "version_number": 1, "display_name": "first-scan.pdf",
              "size_bytes": 1024, "uploaded_at": NOW}]
    page["files"] = present_files(files, status)
    return page


class ReviewRouteCase(DocumentRouteCase):
    PATHS = {
        "/document-requests/8/accept": ("get", "post"),
        "/document-requests/8/correction": ("get", "post"),
        "/document-requests/8/files/9": ("get",),
    }
    ACCEPT = "/document-requests/8/accept"
    CORRECTION = "/document-requests/8/correction"
    DETAIL = "/document-requests/8"

    def setUp(self):
        super().setUp()
        self.request_page = review_page()
        for name in ("accept_document", "request_correction", "open_file_for_staff"):
            patcher = patch.object(routes, name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.sign_in()

    def post(self, path, **changes):
        number = self.open_form(path)
        data = {**FORM, "form_number": number, "file_id": "9", "reason_note": REASON, **changes}
        return self.client.post(path, data=data)

    def flashes(self):
        with self.client.session_transaction() as session:
            return dict(session.get("_flashes", [])).get("document_requests", "")


class ReviewAuthorizationTests(ReviewRouteCase):
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        with self.client.session_transaction() as session:
            session.clear()
        for path, methods in self.PATHS.items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    response = getattr(self.client, method)(path, data=FORM)
                    self.assertEqual((response.status_code, response.location), (302, "/login"))

    def test_administrators_are_refused_everywhere(self):
        self.sign_in("system_administrator")
        for path, methods in self.PATHS.items():
            for method in methods:
                with self.subTest(path=path, method=method):
                    self.assertEqual(getattr(self.client, method)(path, data=FORM).status_code, 403)
        for name in ("accept_document", "request_correction", "open_file_for_staff"):
            self.mocks[name].assert_not_called()

    def test_every_decision_needs_a_valid_csrf_token(self):
        for path in (self.ACCEPT, self.CORRECTION):
            with self.subTest(path=path):
                number = self.open_form(path)
                response = self.client.post(path, data={
                    **FORM, "csrf_token": "forged", "form_number": number, "file_id": "9",
                    "reason_note": REASON})
                self.assertEqual(response.status_code, 400)
        self.mocks["accept_document"].assert_not_called()
        self.mocks["request_correction"].assert_not_called()


class RequestPageFilesTests(ReviewRouteCase):
    def test_the_page_lists_every_version_and_marks_the_latest(self):
        text = self.main_text(self.client.get(self.DETAIL))
        for words in ("Uploaded Files", "second-scan.pdf", "first-scan.pdf", "Version 2",
                      "Version 1", "Latest file", "/document-requests/8/files/9",
                      "/document-requests/8/files/4"):
            self.assertIn(words, text)
        self.assertEqual(text.count("Latest file"), 1)

    def test_review_buttons_appear_only_while_a_file_is_ready_for_review(self):
        self.assertIn("Accept Document", self.main_text(self.client.get(self.DETAIL)))
        for status in ("requested", "needs_correction", "accepted", "cancelled"):
            with self.subTest(status=status):
                self.request_page = review_page(status)
                text = self.main_text(self.client.get(self.DETAIL))
                self.assertNotIn("Accept Document", text)
                self.assertNotIn("Needs Correction</a>", text)

    def test_a_request_without_files_says_so(self):
        self.request_page = request_page("requested")
        self.request_page["files"] = []
        self.assertIn("No file has been uploaded yet.", self.main_text(self.client.get(self.DETAIL)))

    def test_download_links_never_point_to_static_files(self):
        self.assertNotIn("/static/uploads", self.text(self.client.get(self.DETAIL)))


class AcceptPageTests(ReviewRouteCase):
    def test_the_page_names_the_latest_file_and_warns_that_it_is_final(self):
        response = self.client.get(self.ACCEPT)
        text = self.main_text(response)
        for words in ("File being reviewed", "second-scan.pdf (version 2)", "Valid ID",
                      "cannot be undone", "does not approve any subsidy"):
            self.assertIn(words, text)
        self.assertIn('name="file_id" value="9"', self.text(response))

    def test_a_request_that_is_not_ready_returns_with_a_message(self):
        for status in ("requested", "needs_correction", "accepted", "cancelled"):
            with self.subTest(status=status):
                self.request_page = review_page(status)
                response = self.client.get(self.ACCEPT)
                self.assertEqual((response.status_code, response.location), (303, self.DETAIL))
        self.mocks["accept_document"].assert_not_called()

    def test_accepting_calls_the_service_with_the_file_that_was_shown(self):
        response = self.post(self.ACCEPT)
        self.assertEqual((response.status_code, response.location), (303, self.DETAIL))
        self.assertEqual(self.mocks["accept_document"].call_args.args, (5, 8, 3, 7, 9))
        self.assertIn("accepted", self.flashes())

    def test_a_newer_file_returns_to_the_request_without_accepting(self):
        self.mocks["accept_document"].side_effect = CitizenAccountError("file_changed")
        response = self.post(self.ACCEPT)
        self.assertEqual((response.status_code, response.location), (303, self.DETAIL))
        self.assertIn("newer file", self.flashes())

    def test_a_request_decided_meanwhile_is_not_changed(self):
        self.mocks["accept_document"].side_effect = CitizenAccountError("not_reviewable")
        response = self.post(self.ACCEPT)
        self.assertEqual(response.location, self.DETAIL)
        self.assertIn("not waiting for review", self.flashes())

    def test_a_double_click_accepts_only_once(self):
        number = self.open_form(self.ACCEPT)
        data = {**FORM, "form_number": number, "file_id": "9"}
        for _ in range(3):
            self.client.post(self.ACCEPT, data=data)
        self.assertEqual(self.mocks["accept_document"].call_count, 1)

    def test_a_missing_or_malformed_file_number_is_rejected(self):
        for value in ("", "abc", "-1", "9" * 30):
            with self.subTest(value=value):
                self.assertEqual(self.post(self.ACCEPT, file_id=value).status_code, 400)
        self.mocks["accept_document"].assert_not_called()

    def test_service_failures_become_clear_error_pages(self):
        for code, status in (("unauthorized", 403), ("request_missing", 404),
                             ("context_changed", 400), ("unavailable", 503)):
            with self.subTest(code=code):
                self.mocks["accept_document"].side_effect = CitizenAccountError(code)
                self.assertEqual(self.post(self.ACCEPT).status_code, status)


class CorrectionPageTests(ReviewRouteCase):
    def test_the_page_asks_for_a_message_the_citizen_can_act_on(self):
        response = self.client.get(self.CORRECTION)
        text = self.main_text(response)
        for words in ("Request a Correction", "second-scan.pdf (version 2)",
                      "What needs to be fixed?", "Message for the representative",
                      "The representative will read this"):
            self.assertIn(words, text)
        self.assertIn('name="file_id" value="9"', self.text(response))

    def test_a_valid_correction_calls_the_service_and_returns_to_the_request(self):
        response = self.post(self.CORRECTION)
        self.assertEqual((response.status_code, response.location), (303, self.DETAIL))
        arguments = self.mocks["request_correction"].call_args.args
        self.assertEqual(arguments[:5], (5, 8, 3, 7, 9))
        self.assertEqual(arguments[5]["reason_note"], REASON)
        self.assertIn("correction was requested", self.flashes())

    def test_a_missing_reason_shows_the_form_again_with_what_was_typed(self):
        errors = {"reason_note": "Write a reason of 10 to 500 characters."}
        self.mocks["request_correction"].side_effect = DocumentRequestInputError(errors)
        response = self.post(self.CORRECTION, reason_note="no")
        self.assertEqual(response.status_code, 400)
        self.assertIn(errors["reason_note"], self.text(response))
        self.assertIn(">no</textarea>", self.text(response))

    def test_a_final_request_cannot_be_sent_back_for_correction(self):
        self.request_page = review_page("accepted")
        response = self.client.get(self.CORRECTION)
        self.assertEqual((response.status_code, response.location), (303, self.DETAIL))
        self.mocks["request_correction"].assert_not_called()


class DownloadRouteTests(ReviewRouteCase):
    DOWNLOAD = "/document-requests/8/files/9"

    def stored(self, name="second-scan.pdf"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "stored.pdf"
        path.write_bytes(PDF)
        return {"path": path, "display_name": name, "content_type": "application/pdf"}

    def test_staff_get_the_file_as_a_protected_attachment(self):
        self.mocks["open_file_for_staff"].return_value = self.stored()
        response = self.client.get(self.DOWNLOAD)
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PDF)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("second-scan.pdf", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.mocks["open_file_for_staff"].call_args.args, (5, 8, 9))

    def test_a_missing_or_foreign_file_is_not_found(self):
        self.mocks["open_file_for_staff"].side_effect = CitizenAccountError("request_missing")
        self.assertEqual(self.client.get(self.DOWNLOAD).status_code, 404)

    def test_a_file_cannot_be_downloaded_with_a_form_post(self):
        self.assertEqual(self.client.post(self.DOWNLOAD, data=FORM).status_code, 405)


if __name__ == "__main__":
    unittest.main()
