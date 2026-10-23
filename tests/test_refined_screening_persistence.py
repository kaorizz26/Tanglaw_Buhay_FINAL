"""Run 3 checks use synthetic findings and guarded isolated PostgreSQL schemas."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

import test_assessment_queries as fixtures
from repositories import assessment_runs as repository
from scripts import persist_refined_screening_2022 as runner
from services.development_data import development_sources
from services.assessments import build_unified_assessments


def synthetic_expectations(findings):
    """Derive test expectations only from the generated 32-household fixture."""
    counts = {code: Counter() for code in runner.PROGRAMS}
    transitions = {code: Counter() for code in runner.EXPECTED_TRANSITIONS}
    priorities = Counter()
    any_review = 0
    for finding in findings.values():
        statuses = {}
        for code, program in finding['programs'].items():
            status = program['screening_status']
            statuses[code] = status
            counts[code][status] += 1
            if code in transitions:
                transitions[code][(status, status)] += 1
        priorities[runner.derive_subsidy_priority(
            finding['mpi']['vulnerability_score'], statuses,
        )] += 1
        any_review += 'For Review' in statuses.values()
    return dict(HOUSEHOLDS=len(findings), EXPECTED_ANY_REVIEW=any_review,
                EXPECTED_COUNTS={code: tuple(counts[code][s] for s in runner.STATUSES)
                                 for code in counts},
                EXPECTED_TRANSITIONS=transitions, EXPECTED_PRIORITIES=priorities)


class RefinedScreeningUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets, references = development_sources()
        cls.findings = build_unified_assessments(cls.datasets, references, survey_year=2022)

    def test_assembly_copies_complete_mpi_and_calls_screening_once(self):
        baseline = deepcopy(self.findings)
        for finding in baseline.values():
            finding['mpi']['synthetic_nested'] = {'items': [1]}
        before = deepcopy(baseline)
        with patch.multiple(runner, **synthetic_expectations(baseline)), \
                patch.object(runner, 'screen_households', wraps=runner.screen_households) as screen, \
                patch('services.vulnerability.scoring.assess_households', side_effect=AssertionError), \
                patch('psycopg.connect', side_effect=AssertionError):
            actual = runner.build_findings(self.datasets, baseline)
        screen.assert_called_once()
        self.assertEqual(actual, before)
        self.assertEqual(baseline, before)
        for uuid in actual:
            self.assertIsNot(actual[uuid]['mpi'], baseline[uuid]['mpi'])
            actual[uuid]['mpi']['synthetic_nested']['items'].append(2)
        self.assertEqual(baseline, before)

    def test_streamed_fingerprint_matches_original_complete_json(self):
        for value in (self.findings, {'unicode': 'ñ', 'tuple': (1, None), 'nested': {}}):
            expected = sha256(json.dumps(
                value, sort_keys=True, allow_nan=False, default=str,
            ).encode()).hexdigest()
            self.assertEqual(runner.digest(value), expected)
        with self.assertRaises(ValueError):
            runner.digest({'invalid': float('nan')})

    def test_failure_reports_stage_without_private_error_details(self):
        def fail_loading(connection):
            runner.stage('load_run_2_findings')
            raise MemoryError('private source details must not be printed')

        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(runner, 'connect_verified', return_value=connection), \
                patch.object(runner, 'prepare', side_effect=fail_loading), \
                patch('builtins.print') as output:
            self.assertEqual(runner.main(['--check']), 1)
        failure = json.loads(output.call_args.args[0])
        self.assertEqual(failure['stage'], 'load_run_2_findings')
        self.assertEqual(failure['safe_error_code'], 'MemoryError')
        self.assertNotIn('private source details', str(output.call_args_list))

    def test_missing_duplicate_or_wrong_program_results_are_rejected(self):
        rows = [p for f in self.findings.values() for p in f['programs'].values()]
        for invalid in (rows[:-1], rows[:-1] + [rows[0]]):
            with self.subTest(kind=len(invalid)), \
                    patch.multiple(runner, **synthetic_expectations(self.findings)), \
                    patch.object(runner, 'screen_households', return_value=pd.DataFrame(invalid)):
                with self.assertRaisesRegex(runner.VerificationError, 'screening_coverage'):
                    runner.build_findings(self.datasets, self.findings)

    def test_changed_mpi_evidence_or_aggregates_fail(self):
        for kind in ('mpi', 'evidence', 'counts', 'transitions', 'priority'):
            altered = deepcopy(self.findings)
            item = next(iter(altered.values()))
            settings = synthetic_expectations(self.findings)
            if kind == 'mpi':
                item['mpi']['vulnerability_rank'] += 1
            elif kind == 'evidence':
                item['programs']['4Ps']['methodology_notes'] += ('changed',)
            elif kind == 'counts':
                settings['EXPECTED_COUNTS']['SLP'] = (0, 0, 0)
            elif kind == 'transitions':
                settings['EXPECTED_TRANSITIONS']['SLP'] = {}
            else:
                settings['EXPECTED_PRIORITIES'] = {}
            with self.subTest(kind=kind), patch.multiple(runner, **settings):
                with self.assertRaises(runner.VerificationError):
                    runner.verify_findings(altered, self.findings)

    def test_check_is_read_only_and_execute_requires_matching_approval(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        provenance = {'kind': 'synthetic'}
        for arguments, success in ((['--check'], True), (['--execute'], False),
                                   (['--execute', '--approved-sha256', '0' * 64], False)):
            connection.reset_mock()
            with self.subTest(arguments=arguments), \
                    patch.object(runner, 'connect_verified', return_value=connection), \
                    patch.object(runner, 'protected_tables', return_value=[]), \
                    patch.object(runner, 'prepare', return_value=({}, provenance, {})), \
                    patch.object(runner, 'persist_version') as persist, patch('builtins.print'):
                self.assertEqual(runner.main(arguments), 0 if success else 1)
                persist.assert_not_called()
                if success:
                    connection.execute.assert_called_once_with(
                        'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')

    def test_incremental_comparison_rejects_missing_duplicate_and_unexpected_rows(self):
        rows = list(self.findings.values())
        unexpected = {**rows[0], 'household_uuid': 'synthetic_unexpected'}
        for stored in (rows[:-1], rows + [rows[0]], rows + [unexpected]):
            with self.subTest(row_count=len(stored)), \
                    patch.object(repository, 'create_run', return_value=3), \
                    patch.object(repository, 'store_household_assessments'), \
                    patch.object(repository, 'complete_run'), \
                    patch.object(repository, 'iter_household_assessments', return_value=iter(stored)), \
                    patch.object(repository, 'designate_run') as designate:
                with self.assertRaisesRegex(runner.VerificationError, 'stored_complete_findings'):
                    runner.persist_version(MagicMock(), self.findings, {'kind': 'synthetic'}, {})
                designate.assert_not_called()


@unittest.skipUnless(fixtures.TEST_URL, 'Isolated PostgreSQL test URL required.')
class RefinedScreeningPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AssessmentQueryPostgresTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.AssessmentQueryPostgresTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.connection = self.fixture.connection
        self.findings = deepcopy(self.fixture.findings)
        for expected_id in (1, 2):
            self.assertEqual(self.fixture._run(), expected_id)
            self.connection.execute('UPDATE assessment_runs SET calculation_version=%s WHERE id=%s',
                                    (runner.HISTORICAL_VERSIONS[expected_id], expected_id))
        # A synthetic review row exercises preservation independently of UI logic.
        self.connection.execute('CREATE TABLE staff_reviews (id integer, note text)')
        self.connection.execute("INSERT INTO staff_reviews VALUES (1, 'synthetic review')")
        self.snapshot = runner.preservation_snapshot(self.connection)

    def test_atomic_round_trip_designation_and_three_completed_versions(self):
        run_id = runner.persist_version(self.connection, self.findings,
                                        {'kind': 'synthetic'}, self.snapshot)
        self.assertEqual(run_id, 3)
        self.assertEqual(repository.find_designated_run(self.connection, 1)['run_id'], 3)
        self.assertEqual([r['run_id'] for r in repository.find_completed_runs(self.connection, 1)],
                         [1, 2, 3])
        self.assertEqual(runner.preservation_snapshot(self.connection), self.snapshot)

    def test_failures_roll_back_rows_completion_and_designation(self):
        for stage in ('store_household_assessments', 'complete_run', 'designate_run',
                      'find_designated_run'):
            with self.subTest(stage=stage):
                # Each failed attempt consumes sequence 3; reset only this synthetic schema.
                self.connection.execute('ALTER SEQUENCE assessment_runs_id_seq RESTART WITH 3')
                original = getattr(repository, stage)
                def fail_after(*args, **kwargs):
                    original(*args, **kwargs)
                    raise runner.VerificationError('synthetic_failure')
                with patch.object(repository, stage, side_effect=fail_after):
                    with self.assertRaises(runner.VerificationError):
                        runner.persist_version(self.connection, self.findings,
                                               {'kind': 'synthetic'}, self.snapshot)
                self.assertEqual(self.connection.execute('SELECT count(*) FROM assessment_runs').fetchone()[0], 2)
                self.assertEqual(repository.find_designated_run(self.connection, 1)['run_id'], 2)
                self.assertEqual(runner.preservation_snapshot(self.connection), self.snapshot)

    def test_protected_review_change_rolls_back_with_run(self):
        real_store = repository.store_household_assessments
        def tamper(*args):
            real_store(*args)
            self.connection.execute("UPDATE staff_reviews SET note='changed'")
        with patch.object(repository, 'store_household_assessments', side_effect=tamper):
            with self.assertRaisesRegex(runner.VerificationError, 'protected_state_changed'):
                runner.persist_version(self.connection, self.findings, {'kind': 'synthetic'}, self.snapshot)
        self.assertEqual(runner.preservation_snapshot(self.connection), self.snapshot)
        self.assertEqual(repository.find_designated_run(self.connection, 1)['run_id'], 2)

    def test_incremental_complete_payload_mismatch_rolls_back(self):
        original = repository.iter_household_assessments

        def altered_rows(connection, run_id):
            for finding in original(connection, run_id):
                # Keep identity and totals valid while changing nested MPI evidence.
                finding['mpi']['assumptions_used'] += ('synthetic change',)
                yield finding

        with patch.object(repository, 'iter_household_assessments', side_effect=altered_rows):
            with self.assertRaisesRegex(runner.VerificationError, 'stored_complete_findings'):
                runner.persist_version(self.connection, self.findings,
                                       {'kind': 'synthetic'}, self.snapshot)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM assessment_runs').fetchone()[0], 2)
        self.assertEqual(repository.find_designated_run(self.connection, 1)['run_id'], 2)
        self.assertEqual(runner.preservation_snapshot(self.connection), self.snapshot)

    def test_preflight_rejects_pending_attempt_wrong_selection_and_consumed_sequence(self):
        with patch.object(runner, '_batch_metadata', return_value={
                'batch_id': 1, 'survey_year': 2022, 'municipality': 'Pozorrubio'}), \
                patch.object(runner, 'HOUSEHOLDS', 32):
            runner.preflight(self.connection)
            pending = repository.create_run(self.connection, 1, calculation_version=runner.VERSION,
                                             calculation_provenance={'kind': 'synthetic'})
            with self.assertRaisesRegex(runner.VerificationError, 'existing_or_partial'):
                runner.preflight(self.connection)
            self.connection.execute('DELETE FROM assessment_runs WHERE id=%s', (pending,))
            with self.assertRaisesRegex(runner.VerificationError, 'sequence_requires_review'):
                runner.preflight(self.connection)
            self.connection.execute('ALTER SEQUENCE assessment_runs_id_seq RESTART WITH 3')
            repository.designate_run(self.connection, 1, 1)
            with self.assertRaisesRegex(runner.VerificationError, 'run_2_not_designated'):
                runner.preflight(self.connection)


if __name__ == '__main__':
    unittest.main()
