"""Essential version/selection checks; only generated records in isolated test schemas."""

from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

import psycopg

import test_assessment_queries as query_tests
from repositories import assessment_runs as repository
from scripts import persist_corrected_mpi_2022 as runner
from services import assessment_queries as queries
from services.development_data import development_sources


class CorrectedVersionUnitTests(unittest.TestCase):
    def test_check_never_calculates_migrates_or_persists(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(runner, "connect_verified", return_value=connection), \
                patch.object(runner, "preflight", return_value=({}, {}, False)), \
                patch.object(runner, "preservation_snapshot", return_value={}), \
                patch.object(runner, "file_hashes", return_value={}), \
                patch.object(runner, "execute_once") as execute, patch('builtins.print'):
            self.assertEqual(runner.main(["--check"]), 0)
        execute.assert_not_called()
        connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )

    def test_corrected_assembly_copies_every_program_and_never_calls_subsidy_engine(self):
        datasets, references = development_sources()
        # Generate approved output structures; this test never connects to PostgreSQL.
        from services.assessments import build_unified_assessments
        baseline = build_unified_assessments(datasets, references, survey_year=2022)
        before = deepcopy(baseline)
        with patch('psycopg.connect', side_effect=AssertionError('No database connections')), \
                patch('services.subsidy_screening.screen_households', side_effect=AssertionError('No subsidy rerun')):
            findings, summary = runner.build_corrected_findings(datasets, references, baseline, 2022)
        self.assertEqual(baseline, before)
        self.assertEqual(len(findings), 32)
        self.assertEqual(summary['screening']['results'], 128)
        for uuid, original in baseline.items():
            self.assertEqual(findings[uuid]['programs'], original['programs'])
            self.assertIsNot(findings[uuid]['programs'], original['programs'])

    def test_preflight_rejects_existing_or_partial_replacement(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = (1, 19304, 77216)
        metadata = {"batch_id": 1, "survey_year": 2022, "municipality": "Pozorrubio"}
        baseline = {"batch_id": 1, "status": "completed", "calculation_version": runner.BASELINE_VERSION,
                    "expected_household_count": 19304}
        with patch.object(runner, "verify_schema"), \
                patch.object(runner, "assessment_schema_present", return_value=True), \
                patch.object(runner, "_batch_metadata", return_value=metadata), \
                patch.object(runner, "table_counts", return_value=runner.EXPECTED), \
                patch.object(repository, "find_run", return_value=baseline):
            with self.assertRaisesRegex(runner.VerificationError, "existing_replacement_or_baseline_coverage_error"):
                runner.preflight(connection)


@unittest.skipUnless(query_tests.TEST_URL, "Set the local isolated test database URL.")
class CorrectedVersionPostgresTests(unittest.TestCase):
    """Reuse the existing guarded synthetic query fixture, without inheriting its tests."""

    @classmethod
    def setUpClass(cls):
        query_tests.AssessmentQueryPostgresTests.setUpClass()

    def setUp(self):
        self.fixture = query_tests.AssessmentQueryPostgresTests('test_service_calls_leave_rows_unchanged_and_reject_writes')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.connection = self.fixture.connection
        self.batch_id = self.fixture.batch_id
        self.original = deepcopy(self.fixture.findings)
        self.old_run = self.fixture._run()

    def test_new_version_designation_preserves_history_and_all_programs(self):
        new_run = runner.persist_version(self.connection, self.batch_id, self.original, {'kind': 'synthetic-v2'})
        result = queries.get_active_assessment_context(connection_factory=self.fixture._factory)
        self.assertEqual(result['run_id'], new_run)
        self.assertEqual(result['calculation_version'], runner.VERSION)
        self.assertNotEqual(new_run, self.old_run)
        for uuid, expected in self.original.items():
            self.assertEqual(repository.find_household_assessment(self.connection, self.old_run, uuid), expected)
            self.assertEqual(repository.find_household_assessment(self.connection, new_run, uuid), expected)

    def test_designation_rejects_pending_or_cross_batch_runs(self):
        pending = self.fixture._run(complete=False)
        other_batch = self.fixture._batch()
        other_run = self.fixture._run(other_batch)
        for run_id in (pending, other_run):
            with self.subTest(run_id=run_id), self.assertRaises(repository.AssessmentStorageError):
                repository.designate_run(self.connection, self.batch_id, run_id)
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connection.transaction():
                self.connection.execute('UPDATE assessment_run_selections SET run_id=%s WHERE batch_id=%s',
                                        (other_run, self.batch_id))
        self.assertEqual(repository.find_designated_run(self.connection, self.batch_id)['run_id'], self.old_run)

    def test_pending_selection_via_privileged_sql_is_not_exposed(self):
        pending = self.fixture._run(complete=False)
        self.connection.execute('UPDATE assessment_run_selections SET run_id=%s WHERE batch_id=%s',
                                (pending, self.batch_id))
        self.assertIsNone(repository.find_designated_run(self.connection, self.batch_id))
        with self.assertRaisesRegex(queries.AssessmentQueryError, 'approved_assessment_run_missing'):
            queries.get_active_assessment_context(connection_factory=self.fixture._factory)

    def test_failure_after_designation_rolls_back_results_and_pointer(self):
        previous = repository.find_designated_run(self.connection, self.batch_id)
        with patch.object(runner, 'verify_version', side_effect=runner.VerificationError('synthetic_failure')):
            with self.assertRaises(runner.VerificationError):
                runner.persist_version(self.connection, self.batch_id, self.original, {'kind': 'synthetic'})
        self.assertEqual(repository.find_designated_run(self.connection, self.batch_id), previous)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM assessment_runs').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM household_assessments').fetchone()[0], 32)

    def test_migration_and_partial_persistence_roll_back_together(self):
        # This temporary test schema starts the transaction without migration 0004.
        self.connection.execute('DROP TABLE assessment_run_selections')
        invalid = deepcopy(self.original)
        list(invalid.values())[1]['programs'].pop('SLP')
        with self.assertRaises(repository.AssessmentStorageError):
            with self.connection.transaction():
                self.connection.execute(runner.MIGRATION.read_text(encoding='utf-8'))
                runner.persist_version(self.connection, self.batch_id, invalid, {'kind': 'synthetic'})
        exists = self.connection.execute('SELECT to_regclass(%s)',
                                         (f'{self.fixture.schema}.assessment_run_selections',)).fetchone()[0]
        self.assertIsNone(exists)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM assessment_runs').fetchone()[0], 1)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM household_assessments').fetchone()[0], 32)


if __name__ == '__main__':
    unittest.main()
