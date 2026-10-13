"""Synthetic active-batch reconstruction and read-only SQL checks; no database."""

from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg
from psycopg.pq import TransactionStatus

from cbms_csv_fixtures import synthetic_csv_frames
from repositories import dataset_batches as repository
from services.active_batch_loader import ActiveBatchError, load_active_batch
from services.assessments import build_unified_assessments
from services.data_loader import EXCEL_FILES, REFERENCE_FILES
from services.subsidy_screening import screen_households
from services.vulnerability.scoring import assess_households


class ActiveBatchLoaderTests(unittest.TestCase):
    def setUp(self):
        sources, references = synthetic_csv_frames()
        self.sources = {name: frame.astype(str) for name, frame in sources.items()}
        self.references = {name: frame.astype(str) for name, frame in references.items()}
        for frame in self.sources.values():
            frame["uuid"] = frame["uuid"].replace({"DEV-0001": "00002", "DEV-0002": "00001"})
        for name, field in (
            ("section_a_e_indiv", "line_number"),
            ("section_l02_to_l41", "section_l_line_number"),
        ):
            self.sources[name][field] = self.sources[name][field].map(
                lambda value: f"{int(value):03d}",
            )
        members = self.sources["section_a_e_indiv"]
        members.loc[members.index[:2], "a01_middle_name"] = ["", "NA"]
        members.loc[members.index[:2], "a01_suffix"] = ["0007", "0"]
        # Match the existing CSV reader's object dtype, including Pandas 3.
        self.sources = {name: frame.astype(object) for name, frame in self.sources.items()}
        self.references = {name: frame.astype(object) for name, frame in self.references.items()}
        frames = {**self.sources, **self.references}
        filenames = {name: f"{name}.csv" for name in EXCEL_FILES}
        filenames.update(REFERENCE_FILES)
        self.batch = {
            "id": 1, "survey_year": 2022, "municipality": "Pozorrubio",
            "is_active": True, "validation_status": "validated",
            "source_columns": {name: list(frame.columns) for name, frame in frames.items()},
            "source_provenance": {
                "declared": {"kind": "synthetic", "description": "Generated tests only"},
                "files": {name: {"records": len(frame), "filename": filenames[name],
                                 "sha256": "a" * 64} for name, frame in frames.items()},
            },
        }
        # JSONB object-key order differs from the recorded CSV column order.
        self.rows = {
            name: [(number, dict(reversed(list(row.items()))))
                   for number, row in enumerate(frame.to_dict("records"), 1)]
            for name, frame in frames.items()
        }
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.factory = MagicMock(return_value=self.connection)
        guard = patch("psycopg.connect", side_effect=AssertionError("No database connections"))
        guard.start()
        self.addCleanup(guard.stop)

    def _load(self, batches=None, rows=None):
        if batches is None:
            batches = [self.batch]
        if rows is None:
            rows = self.rows

        def records(connection, batch_id, name):
            self.assertIs(connection, self.connection)
            self.assertEqual(batch_id, 1)
            return iter(rows[name])

        with patch.object(repository, "find_active_batches", return_value=batches):
            with patch.object(repository, "iter_batch_records", side_effect=records):
                return load_active_batch(connection_factory=self.factory)

    def test_all_seven_frames_preserve_values_rows_columns_and_identities(self):
        datasets, references, metadata = self._load()
        self.assertEqual(list(datasets), list(EXCEL_FILES))
        self.assertEqual(list(references), list(REFERENCE_FILES))
        for name, original in {**self.sources, **self.references}.items():
            actual = {**datasets, **references}[name]
            pd.testing.assert_frame_equal(actual, original.reset_index(drop=True), check_exact=True)
            self.assertTrue(all(dtype == object for dtype in actual.dtypes))
        self.assertEqual(datasets["household"]["uuid"].tolist(), ["00002", "00001"])
        self.assertEqual(datasets["section_a_e_indiv"]["line_number"].iloc[0], "001")
        self.assertEqual(datasets["section_a_e_indiv"]["a01_middle_name"].iloc[:2].tolist(), ["", "NA"])
        self.assertEqual(datasets["section_a_e_indiv"]["a01_suffix"].iloc[:2].tolist(), ["0007", "0"])
        self.assertEqual(metadata["batch_id"], 1)
        self.assertEqual(metadata["survey_year"], 2022)
        self.assertEqual(metadata["source_provenance"], self.batch["source_provenance"])
        metadata["source_columns"]["household"].reverse()
        self.assertEqual(list(datasets["household"].columns), list(self.sources["household"].columns))
        self.assertEqual(self.batch["source_columns"]["household"], list(self.sources["household"].columns))

    def test_read_only_repeatable_read_and_connection_ownership(self):
        self._load()
        self.factory.assert_called_once_with()
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        self.connection.transaction.assert_called_once_with()
        self.connection.__exit__.assert_called_once()

    def test_non_idle_connection_is_rejected_before_any_sql(self):
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaisesRegex(ActiveBatchError, "active_batch_connection_not_idle"):
            self._load()
        self.connection.execute.assert_not_called()

    def test_no_active_batch_is_a_fixed_safe_failure(self):
        with self.assertRaisesRegex(ActiveBatchError, "^active_batch_missing$"):
            self._load(batches=[])

    def test_invalid_or_ambiguous_active_metadata_is_rejected(self):
        changes = (
            {"id": True}, {"survey_year": 2023}, {"survey_year": True},
            {"municipality": "PRIVATE_VALUE"}, {"is_active": False},
            {"validation_status": "pending"}, {"source_columns": None},
            {"source_columns": {}}, {"source_provenance": {}},
        )
        for change in changes:
            with self.subTest(fields=list(change)):
                batch = {**self.batch, **change}
                with self.assertRaisesRegex(ActiveBatchError, "^invalid_active_batch_metadata$"):
                    self._load(batches=[batch])
        with self.assertRaisesRegex(ActiveBatchError, "^invalid_active_batch_metadata$"):
            self._load(batches=[self.batch, self.batch])

    def test_invalid_columns_and_file_provenance_fail_safely(self):
        for field, value in (("columns", ["uuid", "uuid"]), ("columns", ["uuid", None]),
                             ("columns", [""]), ("records", -1), ("records", True),
                             ("sha256", "PRIVATE_VALUE"), ("filename", "PRIVATE_VALUE")):
            with self.subTest(field=field):
                batch = deepcopy(self.batch)
                if field == "columns":
                    batch["source_columns"]["household"] = value
                else:
                    batch["source_provenance"]["files"]["household"][field] = value
                with self.assertRaisesRegex(ActiveBatchError, "^invalid_active_batch_metadata$"):
                    self._load(batches=[batch])

    def test_missing_extra_or_converted_responses_fail_without_disclosure(self):
        for change in ("missing", "extra", "number", "null"):
            rows = deepcopy(self.rows)
            response = rows["household"][0][1]
            if change == "missing":
                response.pop("uuid")
            elif change == "extra":
                response["PRIVATE_HEADER"] = "PRIVATE_VALUE"
            else:
                response["uuid"] = 2 if change == "number" else None
            with self.subTest(change=change):
                with self.assertRaisesRegex(ActiveBatchError, "^invalid_active_batch_records$"):
                    self._load(rows=rows)

    def test_missing_duplicate_or_unordered_positions_fail(self):
        for positions in ((2, 3), (1, 1), (2, 1)):
            rows = deepcopy(self.rows)
            rows["household"] = [(position, response)
                                 for position, (_, response) in zip(positions, rows["household"])]
            with self.subTest(positions=positions):
                with self.assertRaisesRegex(ActiveBatchError, "^invalid_active_batch_records$"):
                    self._load(rows=rows)

    def test_row_count_mismatch_fails_instead_of_returning_partial_data(self):
        rows = deepcopy(self.rows)
        rows["household"].pop()
        with self.assertRaisesRegex(ActiveBatchError, "^active_batch_record_count_mismatch$"):
            self._load(rows=rows)

    def test_database_error_details_are_suppressed(self):
        self.factory.side_effect = psycopg.OperationalError("PRIVATE_VALUE credentials")
        with self.assertRaisesRegex(ActiveBatchError, "^active_batch_unavailable$") as raised:
            load_active_batch(connection_factory=self.factory)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_reconstructed_synthetic_frames_work_with_all_existing_engines(self):
        datasets, references, metadata = self._load()
        pd.testing.assert_frame_equal(
            assess_households(datasets, references["mpi_mapping"]),
            assess_households(self.sources, self.references["mpi_mapping"]), check_exact=True,
        )
        pd.testing.assert_frame_equal(
            screen_households(datasets, survey_year=metadata["survey_year"]),
            screen_households(self.sources, survey_year=2022), check_exact=True,
        )
        self.assertEqual(
            build_unified_assessments(datasets, references, survey_year=metadata["survey_year"]),
            build_unified_assessments(self.sources, self.references, survey_year=2022),
        )


class ActiveBatchRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value

    def test_active_query_requires_both_active_and_validated(self):
        self.cursor.execute.return_value.fetchall.return_value = []
        self.assertEqual(repository.find_active_batches(self.connection), [])
        query = self.cursor.execute.call_args.args[0]
        self.assertIn("WHERE is_active = true AND validation_status = 'validated'", query)
        self.assertIn("LIMIT 2", query)

    def test_all_table_queries_are_batch_scoped_and_ordered_selects(self):
        tables = {name: table for name, (table, _) in repository.SOURCE_TABLES.items()}
        tables.update(repository.REFERENCE_TABLES)
        self.cursor.__iter__.return_value = iter(())
        for name, table in tables.items():
            list(repository.iter_batch_records(self.connection, 7, name))
            statement, parameters = self.cursor.execute.call_args.args
            query = statement.as_string()
            self.assertIn(f'FROM "{table}"', query)
            self.assertIn("SELECT source_row_number, responses", query)
            self.assertIn("WHERE batch_id = %s ORDER BY source_row_number ASC", query)
            self.assertEqual(parameters, (7,))

    def test_unknown_table_names_are_rejected_without_sql(self):
        with self.assertRaisesRegex(ValueError, "^Unknown batch dataset.$"):
            list(repository.iter_batch_records(self.connection, 1, "PRIVATE_VALUE"))
        self.cursor.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
