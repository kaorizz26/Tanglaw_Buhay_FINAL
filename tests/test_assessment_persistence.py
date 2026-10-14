"""Assessment round trips use generated outputs and transaction-isolated test DDL."""

from copy import deepcopy
import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from repositories import assessment_runs as repository
from services.assessments import build_unified_assessments
from services.development_data import development_sources


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
PROVENANCE = {"kind": "generated synthetic engine outputs", "test_version": "v1"}


class AssessmentSerializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        datasets, references = development_sources()
        cls.findings = build_unified_assessments(datasets, references, survey_year=2022)

    def test_current_output_round_trip_and_no_input_mutation(self):
        before = deepcopy(self.findings)
        for uuid, original in self.findings.items():
            encoded = repository._serialize_findings(original, uuid, 2022)
            restored = repository._restore_findings(encoded)
            self.assertEqual(restored, original)
            self.assertEqual(list(restored["programs"]), ["4Ps", "SLP", "SPISC", "TUPAD"])
        self.assertEqual(self.findings, before)

    def test_incomplete_mismatched_or_nonfinite_payloads_are_rejected(self):
        uuid, original = next(iter(self.findings.items()))
        for case in ("missing_program", "missing_mpi_flag", "wrong_household", "wrong_year", "nan"):
            result = deepcopy(original)
            if case == "missing_program":
                result["programs"].pop("SLP")
            elif case == "missing_mpi_flag":
                result["mpi"].pop("Internet")
            elif case == "wrong_household":
                result["programs"]["SLP"]["household_uuid"] = "SYNTHETIC_OTHER"
            elif case == "wrong_year":
                result["survey_year"] = 2021
            else:
                result["mpi"]["vulnerability_score"] = float("nan")
            with self.subTest(case=case), self.assertRaises(repository.AssessmentStorageError):
                repository._serialize_findings(result, uuid, 2022)

    def test_serialization_uses_source_year_without_a_fixed_year_requirement(self):
        uuid, original = next(iter(self.findings.items()))
        # A past-year metadata-only example; no future dataset/import is created.
        result = deepcopy(original)
        result["survey_year"] = 2021
        for program in result["programs"].values():
            program["survey_year"] = 2021
        encoded = repository._serialize_findings(result, uuid, 2021)
        self.assertEqual(repository._restore_findings(encoded), result)


@unittest.skipUnless(TEST_URL, "Set the local isolated test database URL.")
class AssessmentPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        datasets, references = development_sources()
        cls.findings = build_unified_assessments(datasets, references, survey_year=2022)

    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.assertNotIn("options", settings)
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertFalse(any(os.getenv(name) for name in ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")))
        address = "::1" if settings["host"] == "::1" else "127.0.0.1"
        self.connection = psycopg.connect(TEST_URL, hostaddr=address, connect_timeout=5)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        identity = self.connection.execute(
            "SELECT current_database(), host(inet_server_addr())",
        ).fetchone()
        self.assertEqual(identity, ("tanglaw_buhay_test", address))
        self.schema = f"assessment_check_{uuid4().hex}"
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(
            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)),
        )
        for filename in ("0001_database_foundation.sql", "0002_staff_authentication.sql",
                         "0003_assessment_persistence.sql"):
            self.connection.execute((ROOT / "migrations" / filename).read_text(encoding="utf-8"))
        self.batch_id = self._batch()

    def _batch(self, uuids=None, validated=True):
        uuids = list(self.findings) if uuids is None else uuids
        batch_id = self.connection.execute(
            """INSERT INTO dataset_batches
               (survey_year, municipality, source_columns, source_provenance, validation_status)
               VALUES (2022, 'Pozorrubio', %s, %s, %s) RETURNING id""",
            (Jsonb({"household": ["uuid", "blank", "literal_na", "leading_zero"]}),
             Jsonb({"kind": "generated synthetic test batch"}),
             "validated" if validated else "pending"),
        ).fetchone()[0]
        with self.connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO households (batch_id, uuid, source_row_number, responses)
                   VALUES (%s, %s, %s, %s)""",
                [(batch_id, uuid, number, Jsonb({"uuid": uuid, "blank": "", "literal_na": "NA",
                                               "leading_zero": "0007"}))
                 for number, uuid in enumerate(uuids, 1)],
            )
        return batch_id

    def _run(self, batch_id=None, version="synthetic-test-v1"):
        return repository.create_run(
            self.connection, self.batch_id if batch_id is None else batch_id,
            calculation_version=version, calculation_provenance=PROVENANCE,
        )

    def _completed(self, batch_id=None):
        run_id = self._run(batch_id)
        repository.store_household_assessments(self.connection, run_id, self.findings)
        repository.complete_run(self.connection, run_id)
        return run_id

    def _assert_same_types(self, actual, original):
        self.assertIs(type(actual), type(original))
        if isinstance(original, dict):
            for key in original:
                self._assert_same_types(actual[key], original[key])
        elif isinstance(original, (tuple, list)):
            for value, expected in zip(actual, original):
                self._assert_same_types(value, expected)

    def test_migration_foreign_keys_indexes_and_no_added_year_constraint(self):
        constraints = self.connection.execute(
            """SELECT con.contype, count(*) FROM pg_constraint con
               JOIN pg_class rel ON rel.oid=con.conrelid
               JOIN pg_namespace ns ON ns.oid=rel.relnamespace
               WHERE ns.nspname=%s AND rel.relname IN ('assessment_runs', 'household_assessments')
               GROUP BY con.contype""", (self.schema,),
        ).fetchall()
        self.assertEqual(dict(constraints)["f"], 3)
        self.assertEqual(dict(constraints)["p"], 2)
        generated = self.connection.execute(
            """SELECT is_generated FROM information_schema.columns
               WHERE table_schema=%s AND table_name='household_assessments'
               AND column_name='vulnerability_rank'""", (self.schema,),
        ).fetchone()[0]
        self.assertEqual(generated, "ALWAYS")
        self.assertIsNotNone(self.connection.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND indexname='household_assessments_rank'",
            (self.schema,),
        ).fetchone())
        migration = (ROOT / "migrations/0003_assessment_persistence.sql").read_text()
        self.assertNotIn("2022", migration)
        self.assertNotIn("ALTER TABLE", migration)

    def test_complete_engine_payload_round_trip_preserves_every_value_and_type(self):
        run_id = self._completed()
        run = repository.find_run(self.connection, run_id)
        self.assertEqual(run["batch_id"], self.batch_id)
        self.assertEqual(run["survey_year"], 2022)
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["completed_at"])
        self.assertEqual(run["calculation_provenance"], PROVENANCE)
        for uuid, original in self.findings.items():
            restored = repository.find_household_assessment(self.connection, run_id, uuid)
            self.assertEqual(restored, original)
            self._assert_same_types(restored, original)

    def test_rank_ordering_preserves_original_competition_ties(self):
        run_id = self._completed()
        rows = list(repository.iter_household_assessments(self.connection, run_id))
        expected = sorted(self.findings.values(), key=lambda row: (
            row["mpi"]["vulnerability_rank"], row["household_uuid"],
        ))
        self.assertEqual(rows, expected)
        self.assertEqual(len({row["mpi"]["vulnerability_rank"] for row in rows}),
                         len({row["mpi"]["vulnerability_rank"] for row in self.findings.values()}))

    def test_pending_results_hidden_and_partial_run_cannot_complete(self):
        run_id = self._run()
        first_uuid = next(iter(self.findings))
        repository.store_household_assessments(
            self.connection, run_id, {first_uuid: self.findings[first_uuid]},
        )
        self.assertIsNone(repository.find_household_assessment(self.connection, run_id, first_uuid))
        self.assertEqual(list(repository.iter_household_assessments(self.connection, run_id)), [])
        with self.assertRaisesRegex(repository.AssessmentStorageError, "incomplete_assessment_run"):
            repository.complete_run(self.connection, run_id)
        self.assertEqual(repository.find_run(self.connection, run_id)["status"], "pending")
        rest = {uuid: result for uuid, result in self.findings.items() if uuid != first_uuid}
        repository.store_household_assessments(self.connection, run_id, rest)
        repository.complete_run(self.connection, run_id)
        self.assertIsNotNone(repository.find_household_assessment(self.connection, run_id, first_uuid))

    def test_batches_and_multiple_runs_remain_independent(self):
        first = self._completed()
        other_batch = self._batch()
        second = self._completed(other_batch)
        third = self._run(version="synthetic-test-v2")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(repository.find_run(self.connection, second)["batch_id"], other_batch)
        self.assertEqual(repository.find_run(self.connection, first)["batch_id"], self.batch_id)
        self.assertEqual(repository.find_run(self.connection, third)["status"], "pending")
        uuid = next(iter(self.findings))
        self.assertEqual(repository.find_household_assessment(self.connection, first, uuid),
                         repository.find_household_assessment(self.connection, second, uuid))
        self.assertIsNone(repository.find_household_assessment(self.connection, third, uuid))

    def test_wrong_run_batch_and_wrong_source_household_rejected_by_foreign_keys(self):
        uuids = list(self.findings)
        batch_one = self._batch([uuids[0]])
        batch_two = self._batch([uuids[1]])
        run_id = self._run(batch_one)
        for batch_id, uuid in ((batch_two, uuids[1]), (batch_one, uuids[1])):
            payload = repository._serialize_findings(self.findings[uuid], uuid, 2022)
            with self.subTest(batch=batch_id), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with self.connection.transaction():
                    self.connection.execute(
                        """INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings)
                           VALUES (%s, %s, %s, %s)""", (run_id, batch_id, uuid, Jsonb(payload)),
                    )

    def test_duplicate_household_is_not_overwritten(self):
        run_id = self._run()
        uuid = next(iter(self.findings))
        record = {uuid: self.findings[uuid]}
        repository.store_household_assessments(self.connection, run_id, record)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.connection.transaction():
                repository.store_household_assessments(self.connection, run_id, record)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM household_assessments WHERE run_id=%s", (run_id,),
        ).fetchone()[0], 1)

    def test_completed_run_cannot_be_appended_or_completed_again(self):
        run_id = self._completed()
        before = repository.find_run(self.connection, run_id)
        for operation in (
            lambda: repository.store_household_assessments(self.connection, run_id, self.findings),
            lambda: repository.complete_run(self.connection, run_id),
        ):
            with self.assertRaisesRegex(repository.AssessmentStorageError, "assessment_run_not_pending"):
                operation()
        self.assertEqual(repository.find_run(self.connection, run_id), before)

    def test_transaction_failure_rolls_back_new_run_and_partial_results(self):
        two_records = deepcopy(dict(list(self.findings.items())[:2]))
        list(two_records.values())[1]["programs"].pop("TUPAD")
        with self.assertRaises(repository.AssessmentStorageError):
            with self.connection.transaction():
                run_id = self._run()
                repository.store_household_assessments(self.connection, run_id, two_records)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM assessment_runs").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM household_assessments").fetchone()[0], 0)

    def test_source_rows_unchanged_and_unvalidated_batch_not_accepted(self):
        before = self.connection.execute(
            "SELECT batch_id, uuid, source_row_number, responses FROM households ORDER BY batch_id, source_row_number",
        ).fetchall()
        self._completed()
        after = self.connection.execute(
            "SELECT batch_id, uuid, source_row_number, responses FROM households ORDER BY batch_id, source_row_number",
        ).fetchall()
        self.assertEqual(after, before)
        pending = self._batch(validated=False)
        with self.assertRaisesRegex(repository.AssessmentStorageError, "validated_source_batch_missing"):
            self._run(pending)


if __name__ == "__main__":
    unittest.main()
