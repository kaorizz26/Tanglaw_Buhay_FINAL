"""Contact-number sync tests use generated households and isolated local schemas."""

import csv
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from cbms_csv_fixtures import synthetic_csv_frames, write_csv_fixture
from scripts import sync_household_contact_numbers as sync
from services.active_batch_loader import load_active_batch
from services.cbms_import import import_csv_bundle

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
CONTACT_NUMBERS = ("09123456789", "09000000001")
IMPORT_METADATA = {
    "survey_year": 2022, "municipality": "Pozorrubio",
    "source_provenance": {"kind": "synthetic", "description": "Generated test CSVs only"},
}


def write_contact_csv(path, columns, rows):
    """Write a synthetic household CSV with a BOM, like the real file."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def contact_csv_parts(household_csv):
    """Return (columns, rows) of a generated household CSV with contact_number added."""
    with open(household_csv, encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        columns = next(reader)
        rows = list(reader)
    columns.insert(3, sync.FIELD)
    for row, number in zip(rows, CONTACT_NUMBERS):
        row.insert(3, number)
    return columns, rows


class SourceCsvTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "household.csv"

    def test_leading_zeros_are_kept_as_text(self):
        write_contact_csv(self.path, ["uuid", "contact_number"],
                          [["A", "09123456789"], ["B", "09000000001"]])
        columns, records, digest = sync.read_source_csv(self.path)
        self.assertEqual(columns, ["uuid", "contact_number"])
        self.assertEqual([row["contact_number"] for row in records],
                         ["09123456789", "09000000001"])
        self.assertEqual(len(digest), 64)
        sync.validate_source(records, 2)

    def test_invalid_sources_are_rejected_with_fixed_codes(self):
        cases = {
            "csv_invalid_contact_number": [["A", "9123456789"], ["B", "09000000001"]],
            "csv_invalid_contact_number ": [["A", "0912345678"], ["B", "09000000001"]],
            "csv_duplicate_uuid": [["A", "09123456789"], ["A", "09000000001"]],
            "csv_blank_uuid": [["", "09123456789"], ["B", "09000000001"]],
            "csv_household_count_mismatch": [["A", "09123456789"]],
        }
        for code, rows in cases.items():
            with self.subTest(code=code):
                write_contact_csv(self.path, ["uuid", "contact_number"], rows)
                _, records, _ = sync.read_source_csv(self.path)
                with self.assertRaises(sync.SyncError) as caught:
                    sync.validate_source(records, 2)
                self.assertEqual(str(caught.exception), code.strip())

    def test_missing_contact_column_is_rejected(self):
        write_contact_csv(self.path, ["uuid", "barangay"], [["A", "X"]])
        with self.assertRaises(sync.SyncError):
            sync.read_source_csv(self.path)


class CommandSafetyTests(unittest.TestCase):
    def test_write_requires_confirmation_and_backup_before_connecting(self):
        with TemporaryDirectory() as directory:
            empty_backup = Path(directory) / "backup.dump"
            empty_backup.write_bytes(b"")
            for arguments in (
                ["--execute"],
                ["--execute", "--confirm", "wrong"],
                ["--execute", "--confirm", sync.CONFIRMATION],
                ["--execute", "--confirm", sync.CONFIRMATION, "--backup-file", str(empty_backup)],
            ):
                with self.subTest(arguments=arguments), patch.object(
                    sync, "connect_to_development",
                ) as connect:
                    self.assertEqual(sync.main(arguments), 1)
                    connect.assert_not_called()

    def test_dry_run_is_the_default_and_opens_a_read_only_connection(self):
        with patch.object(sync, "connect_to_development") as connect, patch.object(
            sync, "sync_contact_numbers", return_value={"mode": "dry-run"},
        ) as run:
            self.assertEqual(sync.main([]), 0)
        connect.assert_called_once_with(read_only=True)
        self.assertFalse(run.call_args.kwargs["write"])

    def test_non_development_database_is_refused(self):
        class Config:
            DATABASE_URL = "postgresql://user@localhost/tanglaw_buhay_test"

        with patch("config.Config", Config), self.assertRaises(sync.SyncError):
            sync.connect_to_development(read_only=True)


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic sync tests.")
class ContactNumberSyncDatabaseTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.schema = f"contact_sync_{uuid4().hex}"
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            self.assertEqual(connection.info.dbname, "tanglaw_buhay_test")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.addCleanup(self._drop_schema)
            with connection.transaction():
                connection.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)),
                )
                for path in sorted((ROOT / "migrations").glob("*.sql")):
                    connection.execute(path.read_text("utf-8"))
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        datasets, references = synthetic_csv_frames()
        write_csv_fixture(self.root, datasets, references)
        result = import_csv_bundle(self.root, **IMPORT_METADATA, connection_factory=self._connect)
        self.assertTrue(result["imported"])
        self.batch_id = result["batch_id"]
        self.columns, self.rows = contact_csv_parts(self.root / "raw" / "household.csv")
        self.csv_path = self.root / "household_with_contacts.csv"
        write_contact_csv(self.csv_path, self.columns, self.rows)
        self._add_assessment_and_review_records()

    def _connect(self):
        return psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _add_assessment_and_review_records(self):
        """Insert one synthetic run, finding, selection, staff user and review."""
        uuid = self.rows[0][0]
        findings = {
            "household_uuid": uuid, "survey_year": 2022,
            "mpi": {"uuid": uuid, "vulnerability_rank": 1},
            "programs": {name: {} for name in ("4Ps", "SLP", "SPISC", "TUPAD")},
        }
        with self._connect() as connection:
            run_id = connection.execute(
                """INSERT INTO assessment_runs (batch_id, calculation_version,
                       calculation_provenance, expected_household_count, status, completed_at)
                   VALUES (%s, 'synthetic-v1', %s, 1, 'completed', now()) RETURNING id""",
                (self.batch_id, Jsonb({"kind": "synthetic"})),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings)"
                " VALUES (%s, %s, %s, %s)", (run_id, self.batch_id, uuid, Jsonb(findings)),
            )
            connection.execute(
                "INSERT INTO assessment_run_selections (batch_id, run_id) VALUES (%s, %s)",
                (self.batch_id, run_id),
            )
            staff_id = connection.execute(
                "INSERT INTO staff_users (username, password_hash, role)"
                " VALUES ('synthetic_staff', 'scrypt:x$y$z', 'lgu_staff') RETURNING id",
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO staff_reviews (batch_id, household_uuid, assessment_run_id,
                       program, started_by, responsible_staff_id)
                   VALUES (%s, %s, %s, '4Ps', %s, %s)""",
                (self.batch_id, uuid, run_id, staff_id, staff_id),
            )

    def _run(self, *, write, csv_path=None, expected=2):
        with self._connect() as connection:
            return sync.sync_contact_numbers(
                connection, csv_path or self.csv_path, expected_households=expected, write=write,
            )

    def _snapshot(self):
        """Capture household values, batch metadata and every protected table."""
        with self._connect() as connection:
            return {
                "households": connection.execute(
                    "SELECT uuid, source_row_number, responses FROM households"
                    " ORDER BY source_row_number").fetchall(),
                "batch": connection.execute(
                    "SELECT source_columns, source_provenance, is_active, validation_status"
                    " FROM dataset_batches").fetchall(),
                "protected": sync.protected_fingerprints(connection),
            }

    def _assert_refused(self, code, csv_path=None, expected=2):
        before = self._snapshot()
        with self.assertRaises(sync.SyncError) as caught:
            self._run(write=True, csv_path=csv_path, expected=expected)
        self.assertEqual(str(caught.exception), code)
        self.assertEqual(self._snapshot(), before)

    def test_dry_run_checks_everything_and_changes_nothing(self):
        before = self._snapshot()
        report = self._run(write=False)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["households_checked"], 2)
        self.assertFalse(report["committed"])
        self.assertEqual(self._snapshot(), before)

    def test_write_adds_only_contact_numbers_and_keeps_leading_zeros(self):
        before = self._snapshot()
        report = self._run(write=True)
        self.assertTrue(report["committed"])
        self.assertEqual(report["households_updated"], 2)
        after = self._snapshot()
        for old, new, number in zip(before["households"], after["households"], CONTACT_NUMBERS):
            self.assertEqual(old[:2], new[:2])
            self.assertEqual(new[2][sync.FIELD], number)
            self.assertEqual({k: v for k, v in new[2].items() if k != sync.FIELD}, old[2])
        self.assertEqual(after["protected"], before["protected"])

    def test_metadata_gets_column_position_hash_and_supplemental_note(self):
        before = self._snapshot()
        self._run(write=True)
        (columns, provenance, active, status), = self._snapshot()["batch"]
        (old_columns, old_provenance, _, _), = before["batch"]
        self.assertEqual(columns["household"], self.columns)
        self.assertEqual(columns["household"].index(sync.FIELD), 3)
        self.assertEqual({k: v for k, v in columns.items() if k != "household"},
                         {k: v for k, v in old_columns.items() if k != "household"})
        self.assertEqual(provenance["files"]["household"]["sha256"],
                         sync.read_source_csv(self.csv_path)[2])
        self.assertEqual(provenance["supplemental_fields"]["household"][sync.FIELD],
                         sync.SUPPLEMENTAL_NOTE)
        self.assertEqual(provenance["declared"], old_provenance["declared"])
        self.assertEqual(provenance["files"]["household"]["records"], 2)
        for name in old_provenance["files"]:
            if name != "household":
                self.assertEqual(provenance["files"][name], old_provenance["files"][name])
        self.assertTrue(active)
        self.assertEqual(status, "validated")

    def test_active_batch_still_reconstructs_with_contact_numbers(self):
        self._run(write=True)
        datasets, _, metadata = load_active_batch(self._connect)
        household = datasets["household"]
        self.assertEqual(list(household.columns), self.columns)
        self.assertEqual(list(household[sync.FIELD]), list(CONTACT_NUMBERS))
        self.assertEqual(metadata["batch_id"], self.batch_id)

    def test_second_run_is_refused_because_contact_numbers_exist(self):
        self._run(write=True)
        self._assert_refused("contact_number_already_in_metadata")

    def test_existing_contact_key_in_a_row_is_refused(self):
        with self._connect() as connection:
            connection.execute(
                "UPDATE households SET responses = responses || '{\"contact_number\": \"1\"}'"
                " WHERE source_row_number = 1")
        self._assert_refused("database_differs_from_csv")

    def test_changed_existing_value_is_refused(self):
        rows = [list(row) for row in self.rows]
        rows[1][self.columns.index("barangay")] = "Changed"
        changed = self.root / "changed.csv"
        write_contact_csv(changed, self.columns, rows)
        self._assert_refused("database_differs_from_csv", csv_path=changed)

    def test_missing_unknown_or_reordered_households_are_refused(self):
        unknown = [list(row) for row in self.rows]
        unknown[1][0] = "NOT-IN-DATABASE"
        reordered = [list(self.rows[1]), list(self.rows[0])]
        cases = {
            "unknown_uuid.csv": (unknown, 2, "database_differs_from_csv"),
            "reordered.csv": (reordered, 2, "database_differs_from_csv"),
            "missing_row.csv": ([list(self.rows[0])], 1, "provenance_record_count_mismatch"),
        }
        for name, (rows, expected, code) in cases.items():
            with self.subTest(case=name):
                path = self.root / name
                write_contact_csv(path, self.columns, rows)
                self._assert_refused(code, csv_path=path, expected=expected)

    def test_duplicate_uuid_in_csv_is_refused(self):
        rows = [list(self.rows[0]), list(self.rows[0])]
        path = self.root / "duplicate.csv"
        write_contact_csv(path, self.columns, rows)
        self._assert_refused("csv_duplicate_uuid", csv_path=path)

    def test_inactive_or_ambiguous_batch_is_refused(self):
        with self._connect() as connection:
            connection.execute("UPDATE dataset_batches SET is_active = false")
        self._assert_refused("active_batch_not_unique")

    def test_any_failure_after_the_updates_rolls_everything_back(self):
        for target in ("write_metadata", "verify_written"):
            with self.subTest(target=target):
                before = self._snapshot()
                with patch.object(
                    sync, target, side_effect=sync.SyncError("forced_failure"),
                ), self.assertRaises(sync.SyncError):
                    self._run(write=True)
                self.assertEqual(self._snapshot(), before)

    def test_verification_failure_from_unexpected_change_rolls_back(self):
        real_write = sync.write_contact_numbers

        def write_then_damage(connection, batch_id, records):
            real_write(connection, batch_id, records)
            connection.execute("DELETE FROM staff_reviews")

        before = self._snapshot()
        with patch.object(sync, "write_contact_numbers", write_then_damage):
            with self.assertRaises(sync.SyncError) as caught:
                self._run(write=True)
        self.assertEqual(str(caught.exception), "verify_protected_tables_changed")
        self.assertEqual(self._snapshot(), before)


if __name__ == "__main__":
    unittest.main()
