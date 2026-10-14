"""Stored-query tests use synthetic findings and isolated local PostgreSQL schemas."""

from copy import deepcopy
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from repositories import assessment_runs as repository
from services import assessment_queries as service
from services.assessments import build_unified_assessments
from services.development_data import development_sources


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


class AssessmentQueryServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.factory = MagicMock(return_value=self.connection)
        self.batch = {"id": 9, "survey_year": 2022, "municipality": "Pozorrubio",
                      "validation_status": "validated", "is_active": True,
                      "source_provenance": {"kind": "synthetic"}}
        self.run = {"run_id": 17, "batch_id": 9, "calculation_version": "synthetic-v1",
                    "calculation_provenance": {"kind": "synthetic"},
                    "completed_at": "synthetic timestamp", "household_count": 32}
        mock = patch.object(service.dataset_batches, "find_active_batches", return_value=[self.batch])
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(repository, "find_designated_run", return_value=self.run)
        self.runs = mock.start()
        self.addCleanup(mock.stop)
        mock = patch("psycopg.connect", side_effect=AssertionError("No real connections in unit tests"))
        mock.start()
        self.addCleanup(mock.stop)

    def test_context_and_page_use_one_read_only_snapshot(self):
        with patch.object(repository, "list_assessment_summaries", return_value=[]) as query:
            result = service.list_active_assessment_summaries(
                page=3, page_size=7, connection_factory=self.factory,
            )
        self.assertEqual(result["context"]["batch_id"], 9)
        self.assertEqual(result["context"]["survey_year"], 2022)
        self.assertEqual(result["total"], 32)
        query.assert_called_once_with(self.connection, 17, limit=7, offset=14)
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        self.factory.assert_called_once_with()

    def test_missing_ambiguous_or_invalid_active_batch_fails_safely(self):
        invalid = {**self.batch, "validation_status": "pending"}
        for batches, code in (([], "active_batch_missing"), ([self.batch, self.batch], "active_batch_ambiguous"),
                              ([invalid], "active_batch_invalid")):
            with self.subTest(code=code), \
                    patch.object(service.dataset_batches, "find_active_batches", return_value=batches):
                with self.assertRaisesRegex(service.AssessmentQueryError, code):
                    service.get_active_assessment_context(connection_factory=self.factory)

    def test_identity_lookup_is_bounded_to_page_and_uses_same_snapshot(self):
        items = [{"household_uuid": "SYNTHETIC_B"}, {"household_uuid": "SYNTHETIC_A"}]
        identities = [{"household_uuid": "SYNTHETIC_A"}, {"household_uuid": "SYNTHETIC_B"}]
        with patch.object(repository, "list_assessment_summaries", return_value=items), \
                patch.object(service.dataset_batches, "find_household_identities", return_value=identities) as query:
            result = service.list_active_assessment_summaries(
                page=2, include_household_identity=True, connection_factory=self.factory,
            )
            self.assertEqual([item["household_uuid"] for item in result["items"]],
                             ["SYNTHETIC_B", "SYNTHETIC_A"])
            query.assert_called_once_with(self.connection, 9, ["SYNTHETIC_B", "SYNTHETIC_A"])
            self.factory.assert_called_once()
            query.return_value = []
            with self.assertRaisesRegex(service.AssessmentQueryError, "household_identity_missing"):
                service.list_active_assessment_summaries(
                    include_household_identity=True, connection_factory=self.factory,
                )

    def test_missing_incomplete_or_wrong_batch_designation_is_rejected(self):
        for run, code in ((None, "approved_assessment_run_missing"),
                          ({**self.run, "completed_at": None}, "completed_assessment_run_invalid"),
                          ({**self.run, "batch_id": 8}, "completed_assessment_run_invalid")):
            with self.subTest(code=code):
                self.runs.return_value = run
                with self.assertRaisesRegex(service.AssessmentQueryError, code):
                    service.get_active_assessment_context(connection_factory=self.factory)

    def test_invalid_inputs_are_rejected_before_connecting(self):
        for page, size in ((0, 25), (True, 25), (1, 0), (1, 101), (1, 2.5)):
            with self.subTest(page=page, size=size), self.assertRaises(service.AssessmentQueryError):
                service.list_active_assessment_summaries(
                    page=page, page_size=size, connection_factory=self.factory,
                )
        for uuid in (None, "", "  ", 12):
            with self.subTest(uuid=uuid), self.assertRaises(service.AssessmentQueryError):
                service.get_active_household_assessment(uuid, connection_factory=self.factory)
        self.factory.assert_not_called()

    def test_non_idle_connection_and_database_errors_do_not_leak_values(self):
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaisesRegex(service.AssessmentQueryError, "assessment_connection_not_idle"):
            service.get_active_assessment_context(connection_factory=self.factory)
        self.connection.execute.assert_not_called()
        self.factory.side_effect = psycopg.OperationalError("SYNTHETIC_SECRET")
        with self.assertRaises(service.AssessmentQueryError) as result:
            service.get_active_assessment_context(connection_factory=self.factory)
        self.assertEqual(str(result.exception), "assessment_query_unavailable")

    def test_list_sql_projects_summary_only_and_binds_pagination(self):
        cursor = self.connection.cursor.return_value.__enter__.return_value
        repository.list_assessment_summaries(self.connection, 17, limit=7, offset=14)
        statement, parameters = cursor.execute.call_args.args
        self.assertIn("ORDER BY h.vulnerability_rank ASC, h.household_uuid ASC", statement)
        self.assertIn("r.status = 'completed'", statement)
        self.assertIn("LIMIT %s OFFSET %s", statement)
        self.assertNotIn("SELECT h.findings", statement)
        self.assertNotIn("supporting_evidence", statement)
        self.assertEqual(parameters, (17, 7, 14))


@unittest.skipUnless(TEST_URL, "Set the local isolated test database URL.")
class AssessmentQueryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        datasets, references = development_sources()
        cls.findings = build_unified_assessments(datasets, references, survey_year=2022)

    def _connect(self):
        """Reject non-test targets and verify actual loopback identity before any DDL."""
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.assertIn(settings.get("host"), ("localhost", "127.0.0.1", "::1"))
        self.assertNotIn("service", settings)
        self.assertNotIn("options", settings)
        self.assertIn(settings.get("hostaddr", "127.0.0.1"), ("127.0.0.1", "::1"))
        self.assertFalse(any(os.getenv(name) for name in ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")))
        address = "::1" if settings["host"] == "::1" else "127.0.0.1"
        connection = psycopg.connect(TEST_URL, hostaddr=address, connect_timeout=5, autocommit=True)
        try:
            identity = connection.execute("SELECT current_database(), host(inet_server_addr())").fetchone()
            self.assertEqual(identity, ("tanglaw_buhay_test", address))
            return connection
        except Exception:
            connection.close()
            raise

    def setUp(self):
        self.schema = f"assessment_query_check_{uuid4().hex}"
        self.connection = self._connect()
        self.addCleanup(self.connection.close)
        with self.connection.transaction():
            self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            self.connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(self.schema)))
            for name in ("0001_database_foundation.sql", "0003_assessment_persistence.sql",
                         "0004_assessment_run_selection.sql"):
                self.connection.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.batch_id = self._batch(active=True)

    def _drop_schema(self):
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def _factory(self):
        connection = self._connect()
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        return connection

    def _batch(self, *, active=False, uuids=None):
        uuids = list(self.findings) if uuids is None else uuids
        with self.connection.transaction():
            batch_id = self.connection.execute(
                """INSERT INTO dataset_batches
                   (survey_year, municipality, source_columns, source_provenance, validation_status, is_active)
                   VALUES (2022, 'Pozorrubio', %s, %s, 'validated', %s) RETURNING id""",
                (Jsonb({"household": ["uuid"]}), Jsonb({"kind": "generated synthetic query tests"}), active),
            ).fetchone()[0]
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO households (batch_id, uuid, source_row_number, responses) VALUES (%s, %s, %s, %s)",
                    [(batch_id, uuid, number, Jsonb({"uuid": uuid})) for number, uuid in enumerate(uuids, 1)],
                )
        return batch_id

    def _run(self, batch_id=None, *, complete=True, findings=None, designate=True):
        batch_id = self.batch_id if batch_id is None else batch_id
        findings = self.findings if findings is None else findings
        with self.connection.transaction():
            run_id = repository.create_run(self.connection, batch_id, calculation_version="synthetic-v1",
                                           calculation_provenance={"kind": "generated synthetic query tests"})
            repository.store_household_assessments(self.connection, run_id, findings)
            if complete:
                repository.complete_run(self.connection, run_id)
                if designate:
                    repository.designate_run(self.connection, batch_id, run_id)
        return run_id

    def test_resolution_uses_designation_among_multiple_completed_and_pending_runs(self):
        self._run(complete=False)
        with self.assertRaisesRegex(service.AssessmentQueryError, "approved_assessment_run_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)
        completed = self._run()
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual((result["batch_id"], result["run_id"], result["survey_year"]),
                         (self.batch_id, completed, 2022))
        other = self._run(designate=False)
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual(result["run_id"], completed)
        repository.designate_run(self.connection, self.batch_id, other)
        result = service.get_active_assessment_context(connection_factory=self._factory)
        self.assertEqual(result["run_id"], other)

    def test_missing_or_unvalidated_active_batch_does_not_fall_back(self):
        self._run()
        self.connection.execute("UPDATE dataset_batches SET is_active=false WHERE id=%s", (self.batch_id,))
        with self.assertRaisesRegex(service.AssessmentQueryError, "active_batch_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)
        # A pending inactive source is not a valid alternative.
        other = self._batch()
        self.connection.execute("UPDATE dataset_batches SET validation_status='pending' WHERE id=%s", (other,))
        with self.assertRaisesRegex(service.AssessmentQueryError, "active_batch_missing"):
            service.get_active_assessment_context(connection_factory=self._factory)

    def test_pagination_preserves_ties_and_original_summary_values(self):
        run_id = self._run()
        expected = sorted(self.findings.values(), key=lambda row: (
            row["mpi"]["vulnerability_rank"], row["household_uuid"],
        ))
        actual = []
        for page in range(1, 6):
            result = service.list_active_assessment_summaries(
                page=page, page_size=7, connection_factory=self._factory,
            )
            self.assertEqual(result["context"]["run_id"], run_id)
            self.assertEqual(result["total"], 32)
            self.assertLessEqual(len(result["items"]), 7)
            actual.extend(result["items"])
        self.assertEqual([row["household_uuid"] for row in actual], [row["household_uuid"] for row in expected])
        for row, original in zip(actual, expected):
            for field in ("vulnerability_rank", "vulnerability_score", "coverage_pct",
                          "data_quality_status", "unknown_indicator_count"):
                self.assertEqual(row[field], original["mpi"][field])
            self.assertEqual(row["program_statuses"], {
                code: finding["screening_status"] for code, finding in original["programs"].items()
            })
            self.assertNotIn("findings", row)
        self.assertLess(len({row["vulnerability_rank"] for row in actual}), len(actual))
        empty = service.list_active_assessment_summaries(page=6, page_size=7, connection_factory=self._factory)
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["total"], 32)

    def test_complete_detail_matches_every_synthetic_finding(self):
        self._run()
        for uuid, original in self.findings.items():
            result = service.get_active_household_assessment(uuid, connection_factory=self._factory)
            self.assertEqual(result["findings"], original)
            self.assertIsInstance(result["findings"]["mpi"]["assumptions_used"], tuple)
        with self.assertRaisesRegex(service.AssessmentQueryError, "household_assessment_missing"):
            service.get_active_household_assessment("SYNTHETIC_MISSING", connection_factory=self._factory)

    def test_identities_use_relationship_code_and_batch_scoped_joins(self):
        self._run()
        uuid = min(self.findings, key=lambda key: (
            self.findings[key]["mpi"]["vulnerability_rank"], key,
        ))
        other_batch = self._batch(uuids=[uuid])
        for batch, label in ((self.batch_id, "Active"), (other_batch, "Other")):
            self.connection.execute(
                "UPDATE households SET responses=%s WHERE batch_id=%s AND uuid=%s",
                (Jsonb({"uuid": uuid, "barangay": f"Synthetic {label}"}), batch, uuid),
            )
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO household_members
                       (batch_id, uuid, line_number, source_row_number, responses)
                       VALUES (%s, %s, %s, %s, %s)""",
                    [(batch, uuid, line, line, Jsonb({
                        "a02_relation_to_hh_head": code,
                        "a01_first_name": f"Synthetic {label} {line}",
                        "a01_middle_name": "", "a01_last_name": "Head",
                    })) for line, code in ((1, "2"), (2, "1"))],
                )
        result = service.list_active_assessment_summaries(
            include_household_identity=True, connection_factory=self._factory,
        )
        selected = next(item for item in result["items"] if item["household_uuid"] == uuid)
        self.assertEqual(selected["identity"]["first_name"], "Synthetic Active 2")
        self.assertEqual(selected["identity"]["barangay"], "Synthetic Active")
        self.assertEqual(selected["identity"]["head_count"], 1)
        source_row = self.connection.execute(
            "SELECT source_row_number FROM households WHERE batch_id=%s AND uuid=%s",
            (self.batch_id, uuid),
        ).fetchone()[0]
        self.assertEqual(selected["identity"]["source_row_number"], source_row)
        other_identity = service.dataset_batches.find_household_identities(
            self.connection, other_batch, [uuid],
        )[0]
        self.assertEqual(other_identity["source_row_number"], 1)
        self.assertEqual(len(result["items"]), 25)
        self.assertTrue(any(item["identity"]["head_count"] == 0 for item in result["items"]))
        self.connection.execute(
            """INSERT INTO household_members
               (batch_id, uuid, line_number, source_row_number, responses)
               VALUES (%s, %s, 3, 3, %s)""",
            (self.batch_id, uuid, Jsonb({"a02_relation_to_hh_head": "1", "a01_first_name": "Ambiguous"})),
        )
        identity = service.dataset_batches.find_household_identities(
            self.connection, self.batch_id, [uuid],
        )[0]
        self.assertEqual(identity["head_count"], 2)
        self.assertIsNone(identity["first_name"])
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))
            service.dataset_batches.find_household_identities(connection, self.batch_id, [uuid])

    def test_batch_isolation_uses_active_run_for_shared_and_other_households(self):
        self._run()
        shared_uuid = next(iter(self.findings))
        other_findings = deepcopy(self.findings)
        other_findings[shared_uuid]["mpi"]["assumptions_used"] += ("SYNTHETIC_OTHER_BATCH",)
        other = self._batch()
        other_run = self._run(other, findings=other_findings)
        result = service.get_active_household_assessment(shared_uuid, connection_factory=self._factory)
        self.assertEqual(result["findings"], self.findings[shared_uuid])
        self.connection.execute("UPDATE dataset_batches SET is_active=false WHERE id=%s", (self.batch_id,))
        self.connection.execute("UPDATE dataset_batches SET is_active=true WHERE id=%s", (other,))
        result = service.get_active_household_assessment(shared_uuid, connection_factory=self._factory)
        self.assertEqual(result["context"]["run_id"], other_run)
        self.assertEqual(result["findings"], other_findings[shared_uuid])

    def test_service_calls_leave_rows_unchanged_and_reject_writes(self):
        self._run()
        before = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY run_id, household_uuid)) FROM household_assessments h",
        ).fetchone()
        service.list_active_assessment_summaries(connection_factory=self._factory)
        uuid = next(iter(self.findings))
        service.get_active_household_assessment(uuid, connection_factory=self._factory)
        with service._read_context(self._factory) as (connection, _):
            self.assertEqual(connection.execute("SHOW transaction_read_only").fetchone(), ("on",))
            self.assertEqual(connection.execute("SHOW transaction_isolation").fetchone(), ("repeatable read",))
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                with connection.transaction():
                    connection.execute("DELETE FROM household_assessments")
        after = self.connection.execute(
            "SELECT md5(string_agg(to_jsonb(h)::text, '' ORDER BY run_id, household_uuid)) FROM household_assessments h",
        ).fetchone()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
