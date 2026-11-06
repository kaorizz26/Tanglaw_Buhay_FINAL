"""Document request tables and repository, on isolated schemas with synthetic data only."""

import io
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from repositories import document_requests as repository
from services import document_storage as storage

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
HASH = "a" * 64
PDF = b"%PDF-1.7\nsynthetic document\n%%EOF"


def file_values(version=1, **changes):
    values = {"version_number": version, "display_name": "form.pdf",
              "content_type": "application/pdf", "size_bytes": 100, "sha256": HASH,
              "storage_key": f"{version:032x}.pdf"}
    values.update(changes)
    return values


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic document tests.")
class DocumentRequestTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(conninfo_to_dict(TEST_URL).get("dbname"), "tanglaw_buhay_test")
        self.schema = f"documents_{uuid4().hex}"
        self.db = psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True)
        self.addCleanup(self._drop_schema)
        self.db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.db.execute(path.read_text("utf-8"))
        self._seed()

    def _drop_schema(self):
        self.db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.db.close()

    def _connect(self):
        return psycopg.connect(TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
                               options=f"-c search_path={self.schema}")

    def _seed(self):
        """One survey batch, an Active, a Pending and a Deactivated citizen account, two staff."""
        run = self.db.execute
        self.batch = run(
            "INSERT INTO dataset_batches (survey_year, municipality, source_columns,"
            " source_provenance, validation_status, is_active)"
            " VALUES (2022, 'Pozorrubio', %s, %s, 'validated', true) RETURNING id",
            (Jsonb({}), Jsonb({"kind": "synthetic"}))).fetchone()[0]
        self.accounts = {}
        for number, status in enumerate(("active", "pending_activation", "deactivated", "active"), 1):
            run("INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                " VALUES (%s, %s, %s, %s)", (self.batch, f"H-{number}", number, Jsonb({})))
            password = "'scrypt:x$y$z'" if status != "pending_activation" else "NULL"
            activated = "now()" if status == "active" else "NULL"
            self.accounts[number] = run(
                "INSERT INTO citizen_accounts (batch_id, household_uuid, representative_name, email,"
                f" status, password_hash, activated_at) VALUES (%s, %s, 'Synthetic Citizen', %s, %s,"
                f" {password}, {activated}) RETURNING id",
                (self.batch, f"H-{number}", f"citizen{number}@example.com", status)).fetchone()[0]
        self.active, self.pending, self.deactivated, self.other_active = (
            self.accounts[1], self.accounts[2], self.accounts[3], self.accounts[4])
        self.staff, self.staff_two = [run(
            "INSERT INTO staff_users (username, password_hash, role, is_active)"
            " VALUES (%s, 'scrypt:x$y$z', 'lgu_staff', true) RETURNING id", (name,)).fetchone()[0]
            for name in ("synthetic_staff", "synthetic_staff_two")]

    # ---- helpers ----------------------------------------------------------

    def request(self, account=None, title="Barangay certificate", instructions=None):
        with self._connect() as connection:
            return repository.insert_request(
                connection, account or self.active, self.staff, title, instructions)

    def add_file(self, request_id, version=1, **changes):
        with self._connect() as connection:
            return repository.insert_file(connection, request_id, **file_values(version, **changes))

    def event(self, request_id, action, **keywords):
        with self._connect() as connection:
            return repository.insert_event(connection, request_id, action, **keywords)

    def status_of(self, request_id):
        return self.db.execute("SELECT status FROM document_requests WHERE id = %s",
                               (request_id,)).fetchone()[0]

    def change(self, request_id, status):
        with self._connect() as connection:
            return repository.change_status(connection, request_id, status)

    def refused(self, action, *arguments, **keywords):
        with self.assertRaises(psycopg.Error):
            action(*arguments, **keywords)

    def count(self, table):
        return self.db.execute(sql.SQL("SELECT count(*) FROM {}").format(
            sql.Identifier(table))).fetchone()[0]

    # ---- requests ---------------------------------------------------------

    def test_a_request_is_created_for_an_active_account_in_the_requested_status(self):
        created = self.request(instructions="Bring the original and a copy.")
        self.assertEqual((created["status"], created["account_id"], created["requested_by"]),
                         ("requested", self.active, self.staff))
        self.assertEqual((created["title"], created["instructions"]),
                         ("Barangay certificate", "Bring the original and a copy."))

    def test_only_active_accounts_can_be_asked_for_documents(self):
        for account in (self.pending, self.deactivated, 999999):
            with self.subTest(account=account):
                self.assertIsNone(self.request(account))
        self.assertEqual(self.count("document_requests"), 0)

    def test_titles_and_instructions_must_be_a_sensible_length(self):
        for title in ("", "  ", "ab", "x" * 121):
            with self.subTest(title=title[:10]):
                self.refused(self.request, title=title)
        for instructions in ("", "   ", "x" * 501):
            with self.subTest(instructions=instructions[:10]):
                self.refused(self.request, instructions=instructions)
        self.assertEqual(self.count("document_requests"), 0)
        self.assertIsNotNone(self.request(title="x" * 120, instructions="y" * 500))

    def test_several_requests_can_be_added_in_one_transaction_and_roll_back_together(self):
        titles = ("Valid ID", "Proof of income", "Barangay certificate")

        def add_all(account, fail_after=None):
            with self._connect() as connection:
                with connection.transaction():
                    for position, title in enumerate(titles):
                        created = repository.insert_request(
                            connection, account, self.staff, title, None)
                        if created is None:
                            raise LookupError("account is not active")
                        repository.insert_event(connection, created["id"], "requested",
                                                staff_id=self.staff)
                        if position == fail_after:
                            raise RuntimeError("stop part-way")

        add_all(self.active)
        self.assertEqual((self.count("document_requests"), self.count("document_request_events")), (3, 3))
        for account, fail_after in ((self.other_active, 1), (self.pending, None)):
            with self.assertRaises((RuntimeError, LookupError)):
                add_all(account, fail_after)
        self.assertEqual((self.count("document_requests"), self.count("document_request_events")), (3, 3))

    def test_several_requests_for_one_account_keep_independent_statuses_and_versions(self):
        first, second = self.request(title="Valid ID"), self.request(title="Proof of income")
        self.add_file(first["id"], 1)
        self.change(first["id"], "submitted")
        self.assertEqual((self.status_of(first["id"]), self.status_of(second["id"])),
                         ("submitted", "requested"))
        self.add_file(second["id"], 1, storage_key="b" * 32 + ".pdf")
        with self._connect() as connection:
            self.assertEqual(repository.next_version_number(connection, first["id"]), 2)
            self.assertEqual([item["title"] for item in
                              repository.list_requests_for_account(connection, self.active)],
                             ["Valid ID", "Proof of income"])

    # ---- status steps -----------------------------------------------------

    def test_each_allowed_step_works(self):
        paths = (("submitted", "accepted"), ("submitted", "needs_correction", "submitted", "accepted"),
                 ("cancelled",), ("submitted", "cancelled"),
                 ("submitted", "needs_correction", "cancelled"))
        for path in paths:
            with self.subTest(path=path):
                created = self.request()
                for status in path:
                    self.assertEqual(self.change(created["id"], status)["status"], status)

    def test_steps_that_skip_or_leave_a_final_status_are_refused(self):
        refused_steps = (("requested", "accepted"), ("requested", "needs_correction"),
                         ("requested", "requested"), ("needs_correction", "accepted"),
                         ("accepted", "submitted"), ("accepted", "cancelled"),
                         ("accepted", "needs_correction"), ("cancelled", "requested"),
                         ("cancelled", "submitted"))
        setups = {"requested": (), "needs_correction": ("submitted", "needs_correction"),
                  "accepted": ("submitted", "accepted"), "cancelled": ("cancelled",)}
        for start, target in refused_steps:
            with self.subTest(start=start, target=target):
                created = self.request()
                for status in setups[start]:
                    self.change(created["id"], status)
                self.refused(self.change, created["id"], target)
                self.assertEqual(self.status_of(created["id"]), start)

    def test_only_the_status_of_a_request_can_change_and_it_cannot_be_deleted(self):
        created = self.request()
        for column, value in (("title", "'Another title'"), ("instructions", "'New text'"),
                              ("account_id", self.other_active), ("requested_by", self.staff_two)):
            with self.subTest(column=column):
                self.refused(self.db.execute,
                             f"UPDATE document_requests SET {column} = {value} WHERE id = {created['id']}")
        self.refused(self.db.execute, "DELETE FROM document_requests")
        self.assertEqual(self.count("document_requests"), 1)

    # ---- file versions ----------------------------------------------------

    def test_file_versions_are_numbered_per_request_and_never_repeat(self):
        created = self.request()
        self.add_file(created["id"], 1)
        self.add_file(created["id"], 2)
        self.refused(self.add_file, created["id"], 2, storage_key="c" * 32 + ".pdf")
        with self._connect() as connection:
            self.assertEqual([item["version_number"] for item in
                              repository.list_files(connection, created["id"])], [2, 1])
            self.assertEqual(repository.next_version_number(connection, created["id"]), 3)

    def test_a_request_holds_at_most_ten_versions(self):
        created = self.request()
        for version in range(1, 11):
            self.add_file(created["id"], version)
        self.refused(self.add_file, created["id"], 11)

    def test_file_records_must_describe_an_accepted_file(self):
        created = self.request()
        bad_values = ({"content_type": "text/html"}, {"content_type": "image/gif"},
                      {"size_bytes": 0}, {"size_bytes": 5 * 1024 * 1024 + 1},
                      {"sha256": "XYZ"}, {"display_name": ""}, {"display_name": "x" * 101},
                      {"storage_key": "../evil.pdf"}, {"storage_key": "a" * 32 + ".exe"},
                      {"storage_key": "A" * 32 + ".pdf"}, {"storage_key": "sub/" + "a" * 32 + ".pdf"})
        for change in bad_values:
            with self.subTest(change=change):
                self.refused(self.add_file, created["id"], 1, **change)
        self.assertEqual(self.count("document_files"), 0)
        self.add_file(created["id"], 1, size_bytes=5 * 1024 * 1024)

    def test_a_storage_key_can_belong_to_only_one_file(self):
        first, second = self.request(), self.request(title="Valid ID")
        self.add_file(first["id"], 1)
        self.refused(self.add_file, second["id"], 1)

    def test_file_versions_cannot_be_changed_or_deleted(self):
        created = self.request()
        self.add_file(created["id"], 1)
        self.refused(self.db.execute, "UPDATE document_files SET display_name = 'other.pdf'")
        self.refused(self.db.execute, "DELETE FROM document_files")
        self.assertEqual(self.count("document_files"), 1)

    # ---- history ----------------------------------------------------------

    def lifecycle(self):
        """Request, upload, ask for a correction, replace and accept. Returns (request, files)."""
        created = self.request()
        self.event(created["id"], "requested", staff_id=self.staff)
        files = []
        for version, final in ((1, False), (2, True)):
            files.append(self.add_file(created["id"], version))
            self.event(created["id"], "uploaded", account_id=self.active, file_id=files[-1]["id"])
            self.change(created["id"], "submitted")
            if final:
                self.event(created["id"], "accepted", staff_id=self.staff, file_id=files[-1]["id"])
                self.change(created["id"], "accepted")
            else:
                self.event(created["id"], "correction_requested", staff_id=self.staff_two,
                           file_id=files[-1]["id"], note="The photo is blurred.")
                self.change(created["id"], "needs_correction")
        return created, files

    def test_the_history_records_who_did_what_in_order(self):
        created, files = self.lifecycle()
        with self._connect() as connection:
            history = repository.list_events(connection, created["id"])
        self.assertEqual([item["action"] for item in history],
                         ["requested", "uploaded", "correction_requested", "uploaded", "accepted"])
        self.assertEqual([item["staff_id"] for item in history],
                         [self.staff, None, self.staff_two, None, self.staff])
        self.assertEqual([item["citizen_name"] for item in history],
                         [None, "Synthetic Citizen", None, "Synthetic Citizen", None])
        self.assertEqual(history[2]["staff_name"], "synthetic_staff_two")
        self.assertEqual(history[2]["note"], "The photo is blurred.")
        self.assertEqual(history[4]["file_id"], files[1]["id"])

    def test_every_event_has_exactly_one_actor_and_the_right_details(self):
        created = self.request()
        uploaded = self.add_file(created["id"], 1)
        request_id, file_id = created["id"], uploaded["id"]
        bad_events = (
            ("no actor", dict(action="requested")),
            ("two actors", dict(action="requested", staff_id=self.staff, account_id=self.active)),
            ("citizen requested", dict(action="requested", account_id=self.active)),
            ("staff uploaded", dict(action="uploaded", staff_id=self.staff, file_id=file_id)),
            ("upload without a file", dict(action="uploaded", account_id=self.active)),
            ("accept without a file", dict(action="accepted", staff_id=self.staff)),
            ("correction without a file", dict(action="correction_requested", staff_id=self.staff,
                                               note="Please redo it.")),
            ("correction without a reason", dict(action="correction_requested", staff_id=self.staff,
                                                 file_id=file_id)),
            ("cancel without a reason", dict(action="cancelled", staff_id=self.staff)),
            ("cancel with a file", dict(action="cancelled", staff_id=self.staff, file_id=file_id,
                                        note="No longer needed.")),
            ("blank reason", dict(action="cancelled", staff_id=self.staff, note="  ")),
            ("long reason", dict(action="cancelled", staff_id=self.staff, note="x" * 501)),
            ("unknown action", dict(action="deleted", staff_id=self.staff)),
            ("another citizen's request", dict(action="uploaded", account_id=self.other_active,
                                               file_id=file_id)),
        )
        for label, keywords in bad_events:
            with self.subTest(label):
                action = keywords.pop("action")
                self.refused(self.event, request_id, action, **keywords)
        self.assertEqual(self.count("document_request_events"), 0)

    def test_an_event_cannot_use_a_file_of_another_request(self):
        first, second = self.request(), self.request(title="Valid ID")
        stray = self.add_file(second["id"], 1)
        self.refused(self.event, first["id"], "accepted", staff_id=self.staff, file_id=stray["id"])

    def test_history_cannot_be_changed_or_deleted(self):
        created = self.request()
        self.event(created["id"], "requested", staff_id=self.staff)
        self.refused(self.db.execute, "UPDATE document_request_events SET note = 'edited'")
        self.refused(self.db.execute, "DELETE FROM document_request_events")
        self.assertEqual(self.count("document_request_events"), 1)

    # ---- who can see what -------------------------------------------------

    def test_a_citizen_can_only_find_requests_and_files_of_their_own_account(self):
        mine, theirs = self.request(), self.request(self.other_active, title="Valid ID")
        my_file = self.add_file(mine["id"], 1)
        their_file = self.add_file(theirs["id"], 1, storage_key="d" * 32 + ".pdf")
        with self._connect() as connection:
            self.assertEqual(repository.find_request_for_account(
                connection, mine["id"], self.active)["id"], mine["id"])
            self.assertIsNone(repository.find_request_for_account(
                connection, theirs["id"], self.active))
            self.assertIsNone(repository.find_request_for_account(
                connection, mine["id"], self.other_active))
            self.assertEqual(repository.find_file_for_account(
                connection, my_file["id"], self.active)["storage_key"], my_file["storage_key"])
            self.assertIsNone(repository.find_file_for_account(
                connection, their_file["id"], self.active))
            self.assertEqual([item["id"] for item in
                              repository.list_requests_for_account(connection, self.active)],
                             [mine["id"]])

    def test_the_citizen_list_shows_no_staff_names_or_internal_details(self):
        created = self.request()
        self.event(created["id"], "requested", staff_id=self.staff)
        with self._connect() as connection:
            row = repository.list_requests_for_account(connection, self.active)[0]
        self.assertFalse({"requested_by", "staff_id", "staff_name", "account_id"} & set(row))

    # ---- no effect on assessments ----------------------------------------

    def test_no_document_table_is_linked_to_assessments_or_staff_reviews(self):
        linked = self.db.execute(
            """SELECT DISTINCT referenced.relname FROM pg_constraint c
               JOIN pg_class owner ON owner.oid = c.conrelid
               JOIN pg_class referenced ON referenced.oid = c.confrelid
               WHERE c.contype = 'f' AND owner.relname LIKE 'document_%'
                 AND owner.relnamespace = current_schema()::regnamespace""").fetchall()
        self.assertEqual({row[0] for row in linked},
                         {"citizen_accounts", "staff_users", "document_requests", "document_files"})

    def test_a_full_document_lifecycle_changes_no_assessment_or_review_rows(self):
        watched = ("household_assessments", "assessment_runs", "assessment_run_selections",
                   "staff_reviews", "households", "household_members")
        before = {table: self.count(table) for table in watched}
        self.lifecycle()
        self.assertEqual({table: self.count(table) for table in watched}, before)

    # ---- files and records together --------------------------------------

    def storage_app(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Flask(__name__)
        app.config["DOCUMENT_STORAGE_DIR"] = str(Path(folder.name).resolve())
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        return Path(folder.name)

    def save_upload(self, request_id, data=PDF, fail_with=None):
        """The pattern later tasks use: store the file, then insert its record, in one block."""
        upload = storage.validate_upload("form.pdf", io.BytesIO(data))
        with storage.stored_upload(upload) as key:
            with self._connect() as connection:
                with connection.transaction():
                    version = repository.next_version_number(connection, request_id)
                    record = repository.insert_file(
                        connection, request_id, version, upload.display_name,
                        upload.content_type, upload.size_bytes, upload.sha256, key)
                    if fail_with:
                        raise fail_with
        return record

    def test_a_stored_file_is_found_through_its_record(self):
        folder = self.storage_app()
        created = self.request()
        record = self.save_upload(created["id"])
        self.assertEqual([item.name for item in folder.iterdir()], [record["storage_key"]])
        self.assertEqual(storage.stored_file_path(record["storage_key"]).read_bytes(), PDF)

    def test_a_failed_record_insert_leaves_no_file_and_no_row(self):
        folder = self.storage_app()
        created = self.request()
        with self.assertRaises(RuntimeError):
            self.save_upload(created["id"], fail_with=RuntimeError("insert failed"))
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(self.count("document_files"), 0)

    def test_a_database_refusal_also_leaves_no_file(self):
        folder = self.storage_app()
        self.assertEqual(list(folder.iterdir()), [])
        with self.assertRaises(psycopg.Error):
            self.save_upload(999999)
        self.assertEqual(list(folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
