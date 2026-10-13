"""Transactional importer verification using generated CSVs and a local test DB."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import pandas as pd
import psycopg
from dotenv import load_dotenv
from flask import Flask
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from cbms_csv_fixtures import synthetic_csv_frames, write_csv_fixture
from repositories import dataset_batches as repository
from services.cbms_import import import_csv_bundle
from services.cbms_preflight import load_csv_bundle


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
METADATA = {
    "survey_year": 2022, "municipality": "Pozorrubio",
    "source_provenance": {"kind": "synthetic", "description": "Generated test CSVs only"},
}


class ImportValidationTests(unittest.TestCase):
    def test_failed_preflight_does_not_connect(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            datasets, references = synthetic_csv_frames()
            datasets["household"] = datasets["household"].drop(
                columns=["l06_with_member_currently_pregnant"],
            )
            write_csv_fixture(root, datasets, references)
            factory = Mock()
            result = import_csv_bundle(root, **METADATA, connection_factory=factory)
            factory.assert_not_called()
            self.assertFalse(result["imported"])
            self.assertEqual(result["error"], {"code": "preflight_failed"})
            self.assertNotIn("DEV-0001", json.dumps(result))

    def test_invalid_metadata_does_not_read_files_or_connect(self):
        for change in (
            {"survey_year": 2023}, {"survey_year": True}, {"municipality": "Other"},
            {"source_provenance": {}}, {"source_provenance": {"bad": float("nan")}},
        ):
            with self.subTest(change=change):
                factory = Mock()
                with patch("services.cbms_import.load_csv_bundle") as loader:
                    result = import_csv_bundle(
                        "unused", **{**METADATA, **change}, connection_factory=factory,
                    )
                factory.assert_not_called()
                loader.assert_not_called()
                self.assertEqual(result["error"], {"code": "invalid_import_metadata"})


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic import tests.")
class TransactionalImportTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.schema = f"import_check_{uuid4().hex}"
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            self.assertEqual(connection.info.dbname, "tanglaw_buhay_test")
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.addCleanup(self._drop_schema)
            with connection.transaction():
                connection.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)),
                )
                connection.execute(
                    (ROOT / "migrations" / "0001_database_foundation.sql").read_text("utf-8"),
                )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.datasets, self.references = synthetic_csv_frames()
        write_csv_fixture(self.root, self.datasets, self.references)

    def _connection(self):
        return psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _import(self, factory=None):
        return import_csv_bundle(
            self.root, **METADATA, connection_factory=factory or self._connection,
        )

    def _batch_rows(self, batch_id):
        tables = {name: table for name, (table, _) in repository.SOURCE_TABLES.items()}
        tables.update(repository.REFERENCE_TABLES)
        with self._connection() as connection:
            return {
                name: connection.execute(
                    sql.SQL("""SELECT source_row_number, responses FROM {}
                               WHERE batch_id = %s ORDER BY source_row_number""").format(
                        sql.Identifier(table),
                    ),
                    (batch_id,),
                ).fetchall()
                for name, table in tables.items()
            }

    def _assert_previous_batch_unchanged(self, previous_id, previous_rows):
        with self._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT id, validation_status, is_active FROM dataset_batches ORDER BY id",
            ).fetchall(), [(previous_id, "validated", True)])
        self.assertEqual(self._batch_rows(previous_id), previous_rows)

    def test_commit_preserves_all_values_columns_positions_and_provenance(self):
        for frame in self.datasets.values():
            frame["uuid"] = frame["uuid"].replace({"DEV-0001": "00002", "DEV-0002": "00001"})
        for name, field in (
            ("section_a_e_indiv", "line_number"),
            ("section_l02_to_l41", "section_l_line_number"),
        ):
            self.datasets[name][field] = self.datasets[name][field].map(
                lambda value: f"{value:03d}",
            )
        members = self.datasets["section_a_e_indiv"]
        members.loc[members.index[:2], "a01_middle_name"] = ["", "NA"]
        members.loc[members.index[:2], "a01_suffix"] = ["0", "0007"]
        self.datasets["section_l02_to_l41"] = self.datasets["section_l02_to_l41"].iloc[1:]
        death = self.datasets["section_l11_to_l14"]
        row = dict.fromkeys(death.columns, "")
        row["uuid"] = "00002"
        self.datasets["section_l11_to_l14"] = pd.DataFrame([row, row], columns=death.columns)
        write_csv_fixture(self.root, self.datasets, self.references)
        sources, references, report = load_csv_bundle(self.root)
        self.assertTrue(report["passed"])
        before = {path: path.read_bytes() for path in self.root.rglob("*.csv")}
        result = self._import()
        self.assertTrue(result["imported"], result)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        stored = self._batch_rows(result["batch_id"])
        for name, frame in {**sources, **references}.items():
            self.assertEqual(stored[name], list(enumerate(frame.to_dict("records"), 1)))
        with self._connection() as connection:
            columns, provenance, active, status = connection.execute(
                """SELECT source_columns, source_provenance, is_active, validation_status
                   FROM dataset_batches WHERE id = %s""", (result["batch_id"],),
            ).fetchone()
        self.assertTrue(active)
        self.assertEqual(status, "validated")
        self.assertEqual(provenance["declared"], METADATA["source_provenance"])
        for name, frame in {**sources, **references}.items():
            self.assertEqual(columns[name], list(frame.columns))
            self.assertEqual(provenance["files"][name]["sha256"], frame.attrs["sha256"])
            self.assertEqual(provenance["files"][name]["records"], len(frame))
        self.assertEqual(stored["household"][0][1]["uuid"], "00002")
        self.assertEqual(stored["section_a_e_indiv"][0][1]["line_number"], "001")

    def test_failed_preflight_preserves_existing_active_batch(self):
        first = self._import()
        self.assertTrue(first["imported"], first)
        rows = self._batch_rows(first["batch_id"])
        self.datasets["section_a_e_indiv"].iloc[:0].to_csv(
            self.root / "raw" / "section_a_e_indiv.csv", index=False,
        )
        result = self._import()
        self.assertFalse(result["imported"])
        self.assertEqual(result["error"], {"code": "preflight_failed"})
        self._assert_previous_batch_unchanged(first["batch_id"], rows)

    def test_existing_flask_connection_helper_can_import(self):
        app = Flask("synthetic_import_test")
        app.config["DATABASE_URL"] = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", options=f"-c search_path={self.schema}",
        )
        with app.app_context():
            result = import_csv_bundle(self.root, **METADATA)
        self.assertTrue(result["imported"], result)

    def test_missing_reference_rows_roll_back_before_activation(self):
        first = self._import()
        self.assertTrue(first["imported"], first)
        rows = self._batch_rows(first["batch_id"])
        original = repository._store_frame

        def omit_codebook(connection, batch_id, table, frame, keys):
            if table != "codebook_values":
                original(connection, batch_id, table, frame, keys)

        with patch.object(repository, "_store_frame", side_effect=omit_codebook):
            with patch.object(repository, "activate_batch") as activation:
                result = self._import()
        activation.assert_not_called()
        self.assertEqual(result["error"], {"code": "database_import_failed"})
        self._assert_previous_batch_unchanged(first["batch_id"], rows)

    def test_sql_failure_rolls_back_partial_batch(self):
        first = self._import()
        self.assertTrue(first["imported"], first)
        rows = self._batch_rows(first["batch_id"])
        original = repository._store_frame

        def fail_after_members(connection, batch_id, table, frame, keys):
            original(connection, batch_id, table, frame, keys)
            if table == "household_members":
                connection.execute(
                    """INSERT INTO household_members
                       (batch_id, uuid, line_number, source_row_number, responses)
                       VALUES (%s, 'PRIVATE_SQL_IDENTIFIER', 1, 999, '{}'::jsonb)""",
                    (batch_id,),
                )

        with patch.object(repository, "_store_frame", side_effect=fail_after_members):
            result = self._import()
        self.assertEqual(result["error"], {"code": "database_import_failed"})
        self.assertIsNone(result["batch_id"])
        self.assertNotIn("PRIVATE_SQL_IDENTIFIER", json.dumps(result))
        self._assert_previous_batch_unchanged(first["batch_id"], rows)

    def test_failure_after_activation_restores_old_active_batch(self):
        first = self._import()
        self.assertTrue(first["imported"], first)
        rows = self._batch_rows(first["batch_id"])
        original = repository.activate_batch

        def fail_after_activation(connection, batch_id):
            original(connection, batch_id)
            # A separate reader must still see the committed prior batch.
            with self._connection() as reader:
                self.assertEqual(reader.execute(
                    "SELECT id FROM dataset_batches WHERE is_active",
                ).fetchall(), [(first["batch_id"],)])
            raise RuntimeError("PRIVATE_FAILURE_DETAILS")

        with patch.object(repository, "activate_batch", side_effect=fail_after_activation):
            result = self._import()
        self.assertEqual(result["error"], {"code": "database_import_failed"})
        self.assertNotIn("PRIVATE_FAILURE_DETAILS", json.dumps(result))
        self._assert_previous_batch_unchanged(first["batch_id"], rows)

    def test_replacement_retains_previous_source_rows(self):
        first = self._import()
        self.assertTrue(first["imported"], first)
        rows = self._batch_rows(first["batch_id"])
        self.datasets["household"]["barangay"] = "New synthetic batch"
        write_csv_fixture(self.root, self.datasets, self.references)
        second = self._import()
        self.assertTrue(second["imported"], second)
        with self._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT id, is_active FROM dataset_batches ORDER BY id",
            ).fetchall(), [(first["batch_id"], False), (second["batch_id"], True)])
        self.assertEqual(self._batch_rows(first["batch_id"]), rows)
        self.assertEqual(self._batch_rows(second["batch_id"])["household"][0][1]["barangay"],
                         "New synthetic batch")

    def test_changed_file_does_not_replace_validated_snapshot(self):
        path = self.root / "raw" / "household.csv"
        original_hash = sha256(path.read_bytes()).hexdigest()

        def change_file_then_connect():
            path.write_bytes(b"invalid,replacement\n")
            return self._connection()

        result = self._import(change_file_then_connect)
        self.assertTrue(result["imported"], result)
        self.assertEqual(len(self._batch_rows(result["batch_id"])["household"]), 2)
        with self._connection() as connection:
            provenance = connection.execute(
                "SELECT source_provenance FROM dataset_batches WHERE id = %s",
                (result["batch_id"],),
            ).fetchone()[0]
        self.assertEqual(provenance["files"]["household"]["sha256"], original_hash)

    def test_concurrent_imports_leave_exactly_one_active_batch(self):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda _: self._import(), range(2)))
        self.assertTrue(all(result["imported"] for result in results), results)
        self.assertNotEqual(results[0]["batch_id"], results[1]["batch_id"])
        with self._connection() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*), count(*) FILTER (WHERE is_active) FROM dataset_batches",
            ).fetchone(), (2, 1))


if __name__ == "__main__":
    unittest.main()
