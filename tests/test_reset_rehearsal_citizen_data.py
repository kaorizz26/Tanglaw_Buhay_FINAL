"""Rehearsal reset utility: guards, backup, both scopes and recovery.

Database tests use an isolated synthetic schema in the local test database and a
temporary storage folder. The rehearsal and development databases are never touched.
"""

from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import json

from psycopg.conninfo import conninfo_to_dict

from scripts import reset_rehearsal_citizen_data as reset
import test_document_reviews as reviews

REHEARSAL_URL = "postgresql://staff:secret@localhost:5432/tanglaw_buhay_rehearsal_0015"
ORPHAN_NAME = "f" * 32 + ".pdf"          # looks like a stored file, but no record lists it
PROTECTED_TABLES = ("staff_users", "households", "dataset_batches", "assessment_runs",
                    "household_assessments", "staff_reviews", "staff_review_notes")


class GuardTests(unittest.TestCase):
    def test_only_the_explicit_localhost_rehearsal_database_is_accepted(self):
        settings = reset.rehearsal_settings(REHEARSAL_URL, {})
        self.assertEqual(settings["dbname"], "tanglaw_buhay_rehearsal_0015")
        self.assertEqual(settings["hostaddr"], "127.0.0.1")

    def test_development_test_remote_and_ambiguous_targets_are_refused(self):
        for url in ("", "postgresql://u:p@localhost/tanglaw_buhay_dev",
                    "postgresql://u:p@localhost/tanglaw_buhay_test",
                    "postgresql://u:p@example.com/tanglaw_buhay_rehearsal_0015",
                    "dbname=tanglaw_buhay_rehearsal_0015 host=localhost hostaddr=192.0.2.1 user=u",
                    "dbname=tanglaw_buhay_rehearsal_0015 host=localhost,127.0.0.1 user=u",
                    "dbname=tanglaw_buhay_rehearsal_0015 host=localhost service=other user=u",
                    "dbname=tanglaw_buhay_rehearsal_0015 host=localhost options=-csearch_path=x user=u"):
            with self.subTest(url=url), self.assertRaises(reset.ResetError):
                reset.rehearsal_settings(url, {})
        for environment in ({"PGDATABASE": "other"}, {"PGSERVICE": "other"}):
            with self.assertRaises(reset.ResetError):
                reset.rehearsal_settings(REHEARSAL_URL, environment)

    def test_the_connected_server_must_be_the_rehearsal_database(self):
        settings = reset.rehearsal_settings(REHEARSAL_URL, {})
        good = ["tanglaw_buhay_rehearsal_0015", "127.0.0.1", 5432, "staff", "public"]
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = tuple(good)
        self.assertIn("tanglaw_buhay_rehearsal_0015", reset.verify_target(connection, settings))
        for index, value in ((0, "tanglaw_buhay_dev"), (0, "tanglaw_buhay_test"), (1, "192.0.2.1"),
                             (1, None), (2, 5433), (3, "other"), (4, "other_schema")):
            row = good.copy()
            row[index] = value
            connection.execute.return_value.fetchone.return_value = tuple(row)
            with self.subTest(field=index, value=value), self.assertRaises(reset.ResetError):
                reset.verify_target(connection, settings)

    def test_only_the_approved_storage_folder_is_accepted(self):
        with tempfile.TemporaryDirectory() as approved, tempfile.TemporaryDirectory() as other:
            with patch.object(reset, "EXPECTED_STORAGE", Path(approved)):
                self.assertEqual(reset.check_storage(approved), Path(approved).resolve())
                for configured in (None, "", other, str(Path(approved) / "missing")):
                    with self.subTest(configured=configured), self.assertRaises(reset.ResetError):
                        reset.check_storage(configured)

    def test_a_running_rehearsal_app_stops_the_reset(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with patch.object(reset, "APP_ADDRESS", listener.getsockname()):
                with self.assertRaises(reset.ResetError):
                    reset.refuse_if_app_is_running()
            address = listener.getsockname()
        with patch.object(reset, "APP_ADDRESS", address):  # the port is now closed
            reset.refuse_if_app_is_running()

    def test_it_needs_an_interactive_terminal_and_never_connects_otherwise(self):
        with patch.object(reset.sys.stdin, "isatty", return_value=False), \
                patch.object(reset.psycopg, "connect") as connect, redirect_stderr(io.StringIO()):
            self.assertEqual(reset.main(), 1)
            connect.assert_not_called()
        # A device that claims to be a terminal but is not a console (such as NUL on Windows).
        with patch.object(reset, "_is_real_terminal", return_value=False), \
                patch.object(reset.psycopg, "connect") as connect, redirect_stderr(io.StringIO()):
            self.assertEqual(reset.main(), 1)
            connect.assert_not_called()

    def test_main_refuses_the_development_database_without_connecting(self):
        environment = {"DATABASE_URL": "postgresql://u:p@localhost:5432/tanglaw_buhay_dev"}
        with patch.object(reset, "_is_real_terminal", return_value=True), \
                patch.dict(os.environ, environment), redirect_stderr(io.StringIO()), \
                patch.object(reset.psycopg, "connect") as connect:
            self.assertEqual(reset.main(), 1)
            connect.assert_not_called()

    def test_the_scopes_never_include_staff_household_or_review_tables(self):
        self.assertEqual(set(reset.SCOPES["documents"][1]), set(reset.DOCUMENT_TABLES))
        self.assertEqual(set(reset.SCOPES["all"][1]),
                         set(reset.DOCUMENT_TABLES) | set(reset.CITIZEN_TABLES))
        every_table = set(reset.SCOPES["all"][1])
        self.assertFalse(every_table & set(PROTECTED_TABLES))
        self.assertFalse(any(name.startswith(("staff", "household", "assessment")) for name in every_table))

    def test_scope_choice_defaults_to_documents_only(self):
        self.assertEqual(reset.choose_scope(lambda _: ""), "documents")
        self.assertEqual(reset.choose_scope(lambda _: "1"), "documents")
        self.assertEqual(reset.choose_scope(lambda _: "2"), "all")
        with self.assertRaises(reset.ResetError):
            reset.choose_scope(lambda _: "3")


@unittest.skipUnless(reviews.creation.TEST_URL, "Set the isolated local test database URL.")
class ResetDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.fx = reviews.ReviewServiceTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.settings = {**conninfo_to_dict(reviews.creation.TEST_URL), "hostaddr": "127.0.0.1"}
        self.settings.pop("options", None)
        self.backups = tempfile.TemporaryDirectory(prefix="tanglaw_synthetic_reset_backup_")
        self.addCleanup(self.backups.cleanup)
        self.backup_root = Path(self.backups.name)
        with self.fx._connect() as connection:
            self.tools = reset.postgres_tools(connection.info.server_version)
        # Two requests: Valid ID with two versions, and Birth Certificate with one.
        first, record = self.fx.submitted_request("Valid ID")
        self.fx.correct(first, record["id"])                  # a new version follows a correction
        self.fx.upload(first, version=2, data=reviews.PDF + b" second version")
        self.fx.submitted_request("Birth Certificate")
        (self.fx.storage / ORPHAN_NAME).write_bytes(b"%PDF-1.7 unlisted")
        self.before = self.snapshot()

    # ---- helpers ----------------------------------------------------------

    def snapshot(self):
        """Rows of every table of interest, the stored files, and the protections."""
        tables = reset.SCOPES["all"][1] + PROTECTED_TABLES
        with self.fx._connect() as connection:
            rows = {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in tables}
            triggers = connection.execute(
                "SELECT tgrelid::regclass::text, tgname, tgenabled FROM pg_trigger"
                " WHERE NOT tgisinternal AND tgrelid::regclass::text = ANY(%s) ORDER BY 1, 2",
                (list(reset.SCOPES["all"][1]),)).fetchall()
            sequences = connection.execute(
                "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = %s ORDER BY 1",
                (self.fx.schema,)).fetchall()
        files = {path.name: path.read_bytes() for path in self.fx.storage.iterdir()}
        return {"rows": rows, "files": files, "triggers": triggers, "sequences": sequences}

    def run_reset(self, scope="documents", confirm=lambda _: "wrong", output=lambda _: None):
        with self.fx._connect() as connection:
            return reset._reset_workflow(
                connection, self.fx.schema, self.settings, self.backup_root, self.fx.storage,
                self.tools, scope, confirm=confirm, output=output)

    def phrase(self, scope):
        return f"RESET tanglaw_buhay_test {reset.SCOPES[scope][0]}"

    def assert_protected_tables_unchanged(self, after):
        for table in PROTECTED_TABLES:
            self.assertEqual(after["rows"][table], self.before["rows"][table], table)

    def only_backup(self):
        (folder,) = [path for path in self.backup_root.iterdir() if not path.name.endswith(".incomplete")]
        return folder

    # ---- Documents Only ---------------------------------------------------

    def test_documents_only_removes_requests_and_files_and_keeps_everything_else(self):
        self.assertTrue(self.before["rows"]["document_files"])
        self.assertTrue(self.before["rows"]["citizen_accounts"])
        messages = []
        self.run_reset("documents", confirm=lambda _: self.phrase("documents"), output=messages.append)
        after = self.snapshot()
        for table in reset.DOCUMENT_TABLES:
            self.assertEqual(after["rows"][table], [], table)
        for table in reset.CITIZEN_TABLES:  # accounts stay for the next upload test
            self.assertEqual(after["rows"][table], self.before["rows"][table], table)
        self.assert_protected_tables_unchanged(after)
        self.assertEqual(set(after["files"]), {ORPHAN_NAME})               # unlisted file left alone
        self.assertEqual(after["files"][ORPHAN_NAME], self.before["files"][ORPHAN_NAME])
        self.assertEqual(after["triggers"], self.before["triggers"])         # protections untouched
        self.assertEqual(after["sequences"], self.before["sequences"])       # numbering continues
        self.assertIn("Files with no record (left alone): 1", " ".join(messages))

    def test_all_citizen_data_also_removes_accounts_tokens_and_histories(self):
        self.run_reset("all", confirm=lambda _: self.phrase("all"))
        after = self.snapshot()
        for table in reset.SCOPES["all"][1]:
            self.assertEqual(after["rows"][table], [], table)
        self.assert_protected_tables_unchanged(after)
        self.assertEqual(set(after["files"]), {ORPHAN_NAME})
        self.assertEqual(after["triggers"], self.before["triggers"])
        self.assertEqual(after["sequences"], self.before["sequences"])

    # ---- Confirmation and failures keep everything ------------------------

    def test_the_prompt_names_the_database_and_scope_and_a_wrong_answer_changes_nothing(self):
        prompts = []
        with self.assertRaises(reset.ResetError):
            self.run_reset("all", confirm=lambda prompt: prompts.append(prompt) or "yes")
        self.assertIn(self.phrase("all"), prompts[0])
        self.assertEqual(self.snapshot(), self.before)
        self.assertTrue(self.only_backup().is_dir())                       # verified backup retained

    def test_the_documents_phrase_does_not_confirm_the_larger_scope(self):
        with self.assertRaises(reset.ResetError):
            self.run_reset("all", confirm=lambda _: self.phrase("documents"))
        self.assertEqual(self.snapshot(), self.before)

    def test_a_failed_backup_stops_before_any_confirmation_or_removal(self):
        with patch.object(reset.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pg_dump")), \
                self.assertRaises(reset.ResetError):
            self.run_reset("all", confirm=lambda _: self.fail("confirmation was asked"))
        self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(list(self.backup_root.glob("*.dump")), [])

    def test_an_unverifiable_archive_stops_before_any_confirmation_or_removal(self):
        original = reset.subprocess.run

        def run(arguments, **keywords):
            if "--list" in arguments:
                return subprocess.CompletedProcess(arguments, 0, stdout="unrelated archive")
            return original(arguments, **keywords)

        with patch.object(reset.subprocess, "run", side_effect=run), self.assertRaises(reset.ResetError):
            self.run_reset("documents", confirm=lambda _: self.fail("confirmation was asked"))
        self.assertEqual(self.snapshot(), self.before)

    def test_an_unexpected_stored_file_key_stops_the_reset(self):
        with self.fx._connect() as connection:
            with patch.object(reset, "_listed_files", side_effect=reset.ResetError("bad key")):
                with self.assertRaises(reset.ResetError):
                    reset._reset_workflow(
                        connection, self.fx.schema, self.settings, self.backup_root, self.fx.storage,
                        self.tools, "documents", confirm=lambda _: self.fail("asked"),
                        output=lambda _: None)
        self.assertEqual(self.snapshot(), self.before)

    def test_the_real_test_database_is_not_the_rehearsal_database(self):
        with self.fx._connect() as connection, self.assertRaises(reset.ResetError):
            reset.verify_target(connection, self.settings)

    # ---- Backup contents and recovery -------------------------------------

    def test_the_backup_manifest_describes_the_tables_and_every_file(self):
        self.run_reset("all", confirm=lambda _: self.phrase("all"))
        folder = self.only_backup()
        manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["scope"], "all")
        self.assertEqual(manifest["counts"]["document_files"], 3)
        self.assertEqual(manifest["tables_sha256"], reset._sha256(folder / "tables.dump"))
        self.assertEqual(len(manifest["files"]), 3)
        for item in manifest["files"]:
            self.assertTrue(item["present"])
            self.assertEqual(item["sha256"], reset._sha256(folder / "files" / item["key"]))
        self.assertFalse((folder / "files" / ORPHAN_NAME).exists())      # unlisted files are not copied

    def test_a_missing_stored_file_is_recorded_not_hidden(self):
        missing_key = self.before["rows"]["document_files"][0][7]       # the storage_key column
        (self.fx.storage / missing_key).unlink()
        self.run_reset("documents", confirm=lambda _: self.phrase("documents"))
        manifest = json.loads((self.only_backup() / "manifest.json").read_text("utf-8"))
        self.assertEqual([item["key"] for item in manifest["files"] if not item["present"]], [missing_key])

    def test_recovery_restores_the_records_and_files_exactly(self):
        self.run_reset("all", confirm=lambda _: self.phrase("all"))
        self.assertEqual(self.snapshot()["rows"]["document_files"], [])
        folder = self.only_backup()
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
        environment.update(PGHOST=self.settings["host"], PGHOSTADDR="127.0.0.1",
                           PGPORT=self.settings.get("port", "5432"), PGUSER=self.settings["user"],
                           PGPASSWORD=self.settings.get("password", ""), PGDATABASE=self.settings["dbname"])
        subprocess.run([self.tools[1], "--data-only", "--exit-on-error", "--single-transaction",
                        "--no-owner", "--dbname", self.settings["dbname"], str(folder / "tables.dump")],
                       env=environment, check=True, capture_output=True, timeout=120)
        for path in (folder / "files").iterdir():
            (self.fx.storage / path.name).write_bytes(path.read_bytes())
        self.assertEqual(self.snapshot(), self.before)


if __name__ == "__main__":
    unittest.main()
