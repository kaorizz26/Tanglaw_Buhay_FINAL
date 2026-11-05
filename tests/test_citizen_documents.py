"""Citizen My Documents: list, upload, replace and download.

Uses an isolated schema in the test database and a temporary private storage folder,
with synthetic data only. No email is sent and the development database is untouched.
"""

from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import psycopg

from repositories import document_requests as repository
from services import citizen_documents as service
from services.citizen_documents import DocumentAccessError, present_citizen_request
from test_citizen_portal import PortalTestCase

PDF = b"%PDF-1.7\nsynthetic document\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image"
OTHER_EMAIL = "other@example.com"
LIST_PATH = "/portal/documents/"


def upload_path(request_id):
    return f"/portal/documents/{request_id}/upload"


class PresenterTests(unittest.TestCase):
    def row(self, status):
        return {"id": 1, "title": "Valid ID", "instructions": None, "status": status}

    def test_each_status_has_a_plain_label(self):
        labels = {
            status: present_citizen_request(self.row(status), None, None)["status_label"]
            for status in service.STATUS_LABELS
        }
        self.assertEqual(labels, {
            "requested": "Upload Needed", "submitted": "Waiting for Review",
            "needs_correction": "Needs Correction", "accepted": "Accepted",
            "cancelled": "Cancelled",
        })

    def test_only_open_requests_can_be_uploaded_to(self):
        allowed = {s for s in service.STATUS_LABELS
                   if present_citizen_request(self.row(s), None, None)["can_upload"]}
        self.assertEqual(allowed, {"requested", "needs_correction"})

    def test_a_staff_message_is_shown_only_while_it_still_applies(self):
        correction = {"action": "correction_requested", "note": "Photo is blurry."}
        shown = present_citizen_request(self.row("needs_correction"), None, correction)
        stale = present_citizen_request(self.row("submitted"), None, correction)
        self.assertEqual(shown["reason"], "Photo is blurry.")
        self.assertIsNone(stale["reason"])


class DocumentsTestCase(PortalTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.storage = Path(folder.name).resolve()
        self.app.config["DOCUMENT_STORAGE_DIR"] = str(self.storage)
        self.account = self.make_active()
        self.other_account = self.make_active(OTHER_EMAIL, "H-2")
        self.staff = self.db.execute(
            "INSERT INTO staff_users (username, password_hash, role, is_active)"
            " VALUES ('synthetic_staff', 'scrypt:x$y$z', 'lgu_staff', true) RETURNING id",
        ).fetchone()[0]
        self.other_client = self.app.test_client()
        self.is_signed_in = False

    # --- helpers -------------------------------------------------------------

    def make_request(self, account=None, title="Valid ID", status="requested", instructions=None):
        """Create a request in the wanted status through the allowed status steps."""
        created = repository.insert_request(
            self.db, account or self.account, self.staff, title, instructions)
        repository.insert_event(self.db, created["id"], "requested", staff_id=self.staff)
        steps = {"requested": [], "submitted": ["submitted"], "accepted": ["submitted", "accepted"],
                 "needs_correction": ["submitted", "needs_correction"],
                 "cancelled": ["cancelled"]}[status]
        for step in steps:
            repository.change_status(self.db, created["id"], step)
        return created["id"]

    def staff_message(self, request_id, action, note):
        """Record a staff correction (about an uploaded file) or cancellation."""
        file_id = None
        if action == "correction_requested":
            file_id = (repository.list_files(self.db, request_id) or
                       [self.add_stored_file(request_id, 1)])[0]["id"]
        repository.insert_event(
            self.db, request_id, action, staff_id=self.staff, file_id=file_id, note=note)

    def add_stored_file(self, request_id, version, data=PDF):
        """Record a file version and put its bytes in storage, as an earlier upload would."""
        key = f"{version:032x}.pdf"
        (self.storage / key).write_bytes(data)
        return repository.insert_file(
            self.db, request_id, version, f"scan{version}.pdf", "application/pdf", len(data),
            "a" * 64, key)

    def upload(self, request_id, file_name="id.pdf", data=PDF, client=None, token=None):
        client = client or self.client
        token = token if token is not None else self.csrf(client, LIST_PATH)
        return client.post(upload_path(request_id), data={
            "csrf_token": token, "document": (io.BytesIO(data), file_name),
        }, content_type="multipart/form-data")

    def status_of(self, request_id):
        return self.db.execute(
            "SELECT status FROM document_requests WHERE id = %s", (request_id,)).fetchone()[0]

    def count(self, table):
        return self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def stored_names(self):
        return sorted(item.name for item in self.storage.iterdir())

    def signed_in(self):
        """Sign in once; calling it again in the same test keeps the same session."""
        if not self.is_signed_in:
            self.sign_in()
            self.is_signed_in = True
        return self.client


class AccessTests(DocumentsTestCase):
    def test_every_page_needs_a_citizen_sign_in(self):
        request_id = self.make_request()
        for path in (LIST_PATH, upload_path(request_id), "/portal/documents/files/1"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.headers["Location"].endswith("/portal/login"))
        response = self.client.post(upload_path(request_id))
        self.assertEqual(response.status_code, 302)

    def test_navigation_links_to_my_documents(self):
        self.signed_in()
        self.assertIn("My Documents", self.text(self.client.get("/portal/")))


class ListPageTests(DocumentsTestCase):
    def test_home_counts_documents_that_need_an_upload(self):
        self.make_request()
        self.make_request(title="Proof of income", status="needs_correction")
        self.make_request(title="Barangay certificate", status="accepted")
        self.signed_in()
        page = self.text(self.client.get("/portal/"))
        self.assertIn("2 documents to upload", page)

    def test_home_shows_no_count_when_nothing_is_needed(self):
        self.make_request(status="accepted")
        self.signed_in()
        self.assertNotIn("to upload", self.text(self.client.get("/portal/")))

    def test_each_status_is_written_out_with_its_next_step(self):
        self.make_request(title="Doc A")
        self.make_request(title="Doc B", status="submitted")
        self.make_request(title="Doc C", status="needs_correction")
        self.make_request(title="Doc D", status="accepted")
        self.make_request(title="Doc E", status="cancelled")
        self.signed_in()
        page = self.text(self.client.get(LIST_PATH))
        for label in ("Upload Needed", "Waiting for Review", "Needs Correction", "Accepted",
                      "Cancelled", "Upload File", "Upload Corrected File"):
            self.assertIn(label, page)
        self.assertEqual(page.count('class="portal-button"'), 2)

    def test_correction_and_cancellation_reasons_are_shown(self):
        corrected = self.make_request(title="Doc A", status="needs_correction")
        self.staff_message(corrected, "correction_requested", "The photo is blurry.")
        cancelled = self.make_request(title="Doc B", status="cancelled")
        self.staff_message(cancelled, "cancelled", "Not needed any more.")
        self.signed_in()
        page = self.text(self.client.get(LIST_PATH))
        self.assertIn("The photo is blurry.", page)
        self.assertIn("Not needed any more.", page)

    def test_an_old_correction_reason_is_hidden_after_a_new_upload(self):
        request_id = self.make_request(status="needs_correction")
        self.staff_message(request_id, "correction_requested", "The photo is blurry.")
        self.signed_in()
        self.assertEqual(self.upload(request_id).status_code, 303)
        self.assertNotIn("The photo is blurry.", self.text(self.client.get(LIST_PATH)))

    def test_only_the_citizens_own_requests_and_files_are_listed(self):
        self.make_request(title="Mine")
        other = self.make_request(self.other_account, title="Someone elses document")
        self.add_stored_file(other, 1)
        self.signed_in()
        page = self.text(self.client.get(LIST_PATH))
        self.assertIn("Mine", page)
        self.assertNotIn("Someone elses document", page)
        self.assertNotIn("scan1.pdf", page)

    def test_no_staff_name_or_internal_detail_is_shown(self):
        request_id = self.make_request(status="needs_correction")
        self.staff_message(request_id, "correction_requested", "Please redo it.")
        self.signed_in()
        page = self.text(self.client.get(LIST_PATH))
        self.assertNotIn("synthetic_staff", page)

    def test_the_page_says_so_when_nothing_was_requested(self):
        self.signed_in()
        self.assertIn("has not asked you for any documents",
                      self.text(self.client.get(LIST_PATH)))

    def test_missing_document_tables_show_unavailable_not_an_error_trace(self):
        self.db.execute("DROP TABLE document_request_events, document_files, document_requests CASCADE")
        self.signed_in()
        self.assertEqual(self.client.get(LIST_PATH).status_code, 503)
        home = self.client.get("/portal/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn("to upload", self.text(home))


class UploadTests(DocumentsTestCase):
    def test_upload_page_names_the_allowed_files(self):
        request_id = self.make_request(instructions="Front and back please.")
        self.signed_in()
        page = self.text(self.client.get(upload_path(request_id)))
        self.assertIn("PDF, JPG or PNG, up to 5 MB.", page)
        self.assertIn("Front and back please.", page)
        self.assertIn('type="file"', page)

    def test_first_upload_is_saved_and_waits_for_review(self):
        request_id = self.make_request()
        self.signed_in()
        response = self.upload(request_id)
        self.assertEqual(response.status_code, 303)
        self.assertIn("notice=uploaded", response.headers["Location"])
        self.assertEqual(self.status_of(request_id), "submitted")
        files = repository.list_files(self.db, request_id)
        self.assertEqual([item["version_number"] for item in files], [1])
        self.assertEqual(self.stored_names(), [files[0]["storage_key"]])
        events = repository.list_events(self.db, request_id)
        self.assertEqual([e["action"] for e in events], ["requested", "uploaded"])
        self.assertEqual(events[-1]["account_id"], self.account)
        self.assertEqual(events[-1]["file_id"], files[0]["id"])
        page = self.text(self.client.get(LIST_PATH + "?notice=uploaded"))
        self.assertIn("Waiting for Review", page)
        self.assertIn("Your file was uploaded", page)

    def test_a_correction_upload_adds_a_version_and_keeps_the_old_one(self):
        request_id = self.make_request(status="needs_correction")
        first = self.add_stored_file(request_id, 1)
        self.signed_in()
        self.assertEqual(self.upload(request_id, "new.png", PNG).status_code, 303)
        files = repository.list_files(self.db, request_id)
        self.assertEqual([item["version_number"] for item in files], [2, 1])
        self.assertEqual(len(self.stored_names()), 2)
        self.assertIn(first["storage_key"], self.stored_names())
        self.assertEqual(self.status_of(request_id), "submitted")

    def test_requests_that_are_not_open_refuse_uploads(self):
        for status in ("submitted", "accepted", "cancelled"):
            with self.subTest(status=status):
                request_id = self.make_request(title=f"Doc {status}", status=status)
                self.signed_in()
                response = self.upload(request_id)
                self.assertEqual(response.status_code, 303)
                self.assertIn("notice=closed", response.headers["Location"])
                self.assertEqual(self.status_of(request_id), status)
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.count("document_files"), 0)

    def test_another_households_request_is_not_found(self):
        other = self.make_request(self.other_account)
        self.signed_in()
        for response in (self.client.get(upload_path(other)), self.upload(other)):
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.status_of(other), "requested")

    def test_unknown_request_is_not_found(self):
        self.signed_in()
        self.assertEqual(self.client.get(upload_path(99999)).status_code, 404)

    def test_the_eleventh_version_is_refused_without_saving(self):
        request_id = self.make_request(status="needs_correction")
        for version in range(1, 11):
            self.add_stored_file(request_id, version)
        before = self.stored_names()
        self.signed_in()
        response = self.upload(request_id)
        self.assertEqual(response.status_code, 400)
        self.assertIn("10 times", self.text(response))
        self.assertEqual(self.stored_names(), before)
        self.assertEqual(self.count("document_files"), 10)

    def test_bad_files_get_a_plain_message_and_change_nothing(self):
        request_id = self.make_request()
        self.signed_in()
        cases = {
            ("notes.txt", PDF): "Upload a PDF, JPG or PNG file.",
            ("empty.pdf", b""): "The file is empty.",
            ("fake.pdf", b"not really a pdf"): "does not match its type",
            ("big.pdf", PDF + b"x" * (5 * 1024 * 1024)): "larger than 5 MB",
        }
        for (name, data), message in cases.items():
            with self.subTest(name=name):
                response = self.upload(request_id, name, data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, self.text(response))
        self.assertEqual(self.status_of(request_id), "requested")
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.count("document_files"), 0)

    def test_choosing_no_file_is_explained(self):
        request_id = self.make_request()
        self.signed_in()
        token = self.csrf(self.client, LIST_PATH)
        response = self.client.post(upload_path(request_id), data={"csrf_token": token})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a file to upload.", self.text(response))

    def test_a_missing_or_wrong_csrf_token_saves_nothing(self):
        request_id = self.make_request()
        self.signed_in()
        for token in ("", "wrong"):
            self.assertEqual(self.upload(request_id, token=token).status_code, 400)
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.status_of(request_id), "requested")

    def test_a_deactivated_account_cannot_upload(self):
        request_id = self.make_request()
        self.signed_in()
        token = self.csrf(self.client, LIST_PATH)
        self.db.execute(
            "UPDATE citizen_accounts SET status = 'deactivated', session_version = session_version + 1"
            " WHERE id = %s", (self.account,))
        response = self.upload(request_id, token=token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.stored_names(), [])

    def test_the_account_is_checked_again_inside_the_transaction(self):
        request_id = self.make_request()
        account = self.db.execute(
            "SELECT id, status, session_version FROM citizen_accounts WHERE id = %s",
            (self.account,)).fetchone()
        stale = {"id": account[0], "status": "active", "session_version": account[2]}
        self.db.execute("UPDATE citizen_accounts SET status = 'deactivated' WHERE id = %s",
                        (self.account,))
        with self.app.app_context(), self.assertRaises(DocumentAccessError) as caught:
            service.upload_document(stale, request_id, "id.pdf", io.BytesIO(PDF))
        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(self.stored_names(), [])

    def test_a_request_cancelled_during_the_upload_leaves_no_file(self):
        request_id = self.make_request()
        account = {"id": self.account, "session_version": self.db.execute(
            "SELECT session_version FROM citizen_accounts WHERE id = %s",
            (self.account,)).fetchone()[0]}
        real_check = service._load_open_request
        calls = []

        def cancel_before_second_check(connection, account_, request_id_, *, lock):
            calls.append(lock)
            if lock:  # Staff cancel after the quick check and before the held check.
                repository.change_status(self.db, request_id, "cancelled")
            return real_check(connection, account_, request_id_, lock=lock)

        with patch.object(service, "_load_open_request", cancel_before_second_check):
            with self.app.app_context(), self.assertRaises(DocumentAccessError) as caught:
                service.upload_document(account, request_id, "id.pdf", io.BytesIO(PDF))
        self.assertEqual(calls, [False, True])
        self.assertEqual(caught.exception.code, "not_open")
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.count("document_files"), 0)

    def test_a_failed_save_removes_the_file_and_the_record(self):
        request_id = self.make_request()
        self.signed_in()
        with patch.object(repository, "insert_event", side_effect=psycopg.OperationalError("x")):
            response = self.upload(request_id)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.count("document_files"), 0)
        self.assertEqual(self.status_of(request_id), "requested")

    def test_two_uploads_at_once_save_only_one(self):
        request_id = self.make_request()
        account = {"id": self.account, "session_version": self.db.execute(
            "SELECT session_version FROM citizen_accounts WHERE id = %s",
            (self.account,)).fetchone()[0]}

        def attempt(number):
            with self.app.app_context():
                try:
                    service.upload_document(
                        account, request_id, f"id{number}.pdf", io.BytesIO(PDF + bytes([number])))
                    return "saved"
                except DocumentAccessError as refused:
                    return refused.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(attempt, (1, 2)))
        self.assertEqual(results, ["not_open", "saved"])
        self.assertEqual(self.count("document_files"), 1)
        self.assertEqual(len(self.stored_names()), 1)

    def test_a_file_over_five_megabytes_is_stopped_by_the_request_limit(self):
        request_id = self.make_request()
        self.signed_in()
        token = self.csrf(self.client, LIST_PATH)
        response = self.upload(request_id, "huge.pdf", PDF + b"x" * (6 * 1024 * 1024), token=token)
        self.assertEqual(response.status_code, 413)
        self.assertIn("larger than 5 MB", self.text(response))
        self.assertEqual(self.stored_names(), [])

    def test_the_one_megabyte_limit_still_applies_everywhere_else(self):
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], 1024 * 1024)
        self.signed_in()
        token = self.csrf(self.client, LIST_PATH)
        response = self.client.post("/portal/change-password", data={
            "csrf_token": token, "current_password": "x" * (1024 * 1024 + 10)})
        self.assertEqual(response.status_code, 413)

    def test_uploads_are_unavailable_when_storage_is_not_set_up(self):
        request_id = self.make_request()
        self.app.config["DOCUMENT_STORAGE_DIR"] = None
        self.signed_in()
        response = self.upload(request_id)
        self.assertEqual(response.status_code, 503)
        self.assertIn("Uploads are unavailable", self.text(response))
        self.assertEqual(self.status_of(request_id), "requested")


class DownloadTests(DocumentsTestCase):
    def test_a_citizen_downloads_their_own_file_with_safe_headers(self):
        request_id = self.make_request(status="submitted")
        record = self.add_stored_file(request_id, 1)
        self.signed_in()
        response = self.client.get(f"/portal/documents/files/{record['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PDF)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("scan1.pdf", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_the_list_links_to_the_latest_file_only(self):
        request_id = self.make_request(status="submitted")
        self.add_stored_file(request_id, 1)
        latest = self.add_stored_file(request_id, 2)
        self.signed_in()
        page = self.text(self.client.get(LIST_PATH))
        self.assertIn(f"/portal/documents/files/{latest['id']}", page)
        self.assertIn("scan2.pdf", page)
        self.assertNotIn("scan1.pdf", page)

    def test_someone_elses_file_and_a_missing_file_look_the_same(self):
        other = self.make_request(self.other_account, status="submitted")
        record = self.add_stored_file(other, 1)
        self.signed_in()
        theirs = self.client.get(f"/portal/documents/files/{record['id']}")
        missing = self.client.get("/portal/documents/files/999999")
        huge = self.client.get(f"/portal/documents/files/{2 ** 70}")
        self.assertEqual({theirs.status_code, missing.status_code, huge.status_code}, {404})
        self.assertEqual(theirs.data, missing.data)
        self.assertNotIn(b"%PDF", theirs.data)

    def test_a_record_whose_file_is_gone_is_not_found(self):
        request_id = self.make_request(status="submitted")
        record = self.add_stored_file(request_id, 1)
        (self.storage / record["storage_key"]).unlink()
        self.signed_in()
        self.assertEqual(self.client.get(f"/portal/documents/files/{record['id']}").status_code, 404)

    def test_an_uploaded_file_can_be_downloaded_again(self):
        request_id = self.make_request()
        self.signed_in()
        self.upload(request_id, "my id.pdf", PDF)
        record = repository.list_files(self.db, request_id)[0]
        response = self.client.get(f"/portal/documents/files/{record['id']}")
        self.assertEqual(response.data, PDF)


class UnrelatedDataTests(DocumentsTestCase):
    def test_uploading_changes_no_assessment_or_review_rows(self):
        watched = ("household_assessments", "assessment_runs", "staff_reviews", "households")
        before = {table: self.count(table) for table in watched}
        request_id = self.make_request()
        self.signed_in()
        self.upload(request_id)
        self.assertEqual({table: self.count(table) for table in watched}, before)


if __name__ == "__main__":
    unittest.main()
