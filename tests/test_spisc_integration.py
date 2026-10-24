"""Current SPISC integration: synthetic records, isolated PostgreSQL only."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import unittest
from unittest.mock import patch

from psycopg.types.json import Jsonb

from assessment_detail_fixtures import screening_source
import test_assessment_queries as query_tests
import test_staff_reviews as review_tests
from repositories import assessment_runs, assessment_evidence
from services import assessment_queries
from services.assessment_details import prepare_assessment_detail
from services.assessment_filters import AssessmentFilters
from services.spisc_age_progression import current_spisc_status
from services.subsidy_priority import derive_subsidy_priority
from app import create_app
from services.recommendation_changes import change_recommendation


ASSESSED = date(2026, 10, 24)
AGE_CASES = (['60'], ['56'], ['55'], ['30'], [None], [], ['60', '62'],
             ['60', None], [' 5.5e1 '], ['56.0'], ['NA'], ['55.5'],
             ['٥٥'], ['5_5'], ['NaN'], ['Infinity'], ['-1'], ['2022'],
             [True], [False], [55], [56.0], [[]], [{}])


class CurrentSpiscTests(unittest.TestCase):
    def test_municipal_operation_captures_one_date_and_reuses_one_age_lookup(self):
        fixture = query_tests.AssessmentQueryServiceTests('test_context_and_page_use_one_read_only_snapshot')
        fixture.setUp()
        try:
            with patch.object(assessment_queries, 'philippine_assessment_date', return_value=ASSESSED) as clock, \
                    patch.object(assessment_evidence, 'list_recorded_ages', return_value=['55', '30', None]) as ages, \
                    patch.object(assessment_evidence, 'list_member_sources', side_effect=AssertionError('No rosters')), \
                    patch.object(assessment_runs, 'list_assessment_summaries', return_value=[]) as page, \
                    patch.object(assessment_runs, 'count_assessment_summaries', return_value=0) as count, \
                    patch.object(assessment_runs, 'count_priority_groups', return_value={}) as groups:
                result = assessment_queries.list_active_assessment_summaries(
                    filters=AssessmentFilters(programs=('SPISC',)), include_priority_counts=True,
                    connection_factory=fixture.factory)
                clock.assert_called_once_with()
                ages.assert_called_once_with(fixture.connection, 9)
                self.assertEqual(result['assessment_date'], ASSESSED)
                for query in (page, count, groups):
                    self.assertEqual(query.call_args.kwargs['spisc_age_statuses'], {
                        '55': 'For Review', '30': 'No Historical CBMS Trigger'})
        finally:
            fixture.doCleanups()

    def test_policy_matches_detail_and_preserves_historical_findings(self):
        for ages in AGE_CASES:
            with self.subTest(ages=ages):
                source = screening_source()
                template = source['members'][0]
                source['members'] = []
                for line, age in enumerate(ages, 1):
                    member = deepcopy(template)
                    member['line_number'] = line
                    member['responses']['a07_age'] = age
                    member['health_present'] = False
                    source['members'].append(member)
                for code, finding in source['findings']['programs'].items():
                    finding['screening_status'] = 'No Historical CBMS Trigger'
                before = deepcopy(source)
                result = prepare_assessment_detail(source, assessment_date=ASSESSED)
                expected = current_spisc_status(ages, source['context'], ASSESSED)
                self.assertEqual(result['programs'][2]['status'], expected)
                self.assertEqual(result['screening_programs'][2]['guidance']['current_status'], expected)
                self.assertEqual(result['screening_programs'][2]['status'], 'No Historical CBMS Trigger')
                self.assertEqual(source, before)
                for program in result['programs']:
                    if program['name'] != 'SPISC':
                        self.assertEqual(program['status'], 'No Historical CBMS Trigger')
                self.assertEqual(result['priority_display']['label'],
                                 'Primary Priority' if expected == 'For Review'
                                 else 'Not in Priority Groups')

    def test_calendar_reference_rules_and_supported_precedence(self):
        context = {'survey_year': 2022}
        for assessed, expected in ((date(2026, 6, 30), 'No Historical CBMS Trigger'),
                                   (date(2026, 7, 1), 'For Review')):
            self.assertEqual(current_spisc_status(['55'], context, assessed), expected)
        self.assertEqual(current_spisc_status(['55', None], context, ASSESSED), 'For Review')
        self.assertEqual(current_spisc_status(['60'], context, date(2022, 6, 29)), 'Needs Data Review')
        future = {'survey_year': 2024, 'source_provenance': {'declared': {
            'age_reference_date': '2024-12-31', 'age_reference_date_verified': True}}}
        self.assertEqual(current_spisc_status(['56'], future, date(2027, 10, 24)),
                         'No Historical CBMS Trigger')
        future['source_provenance']['declared']['age_reference_date_verified'] = False
        self.assertEqual(current_spisc_status(['56'], future, date(2027, 10, 24)), 'For Review')


@unittest.skipUnless(query_tests.TEST_URL, 'Set the isolated local test database URL.')
class CurrentSpiscQueryDatabaseTests(unittest.TestCase):
    # Borrow only fixture helpers; importing the module avoids duplicate discovery.
    setUpClass = classmethod(query_tests.AssessmentQueryPostgresTests.setUpClass.__func__)
    setUp = query_tests.AssessmentQueryPostgresTests.setUp
    _connect = query_tests.AssessmentQueryPostgresTests._connect
    _drop_schema = query_tests.AssessmentQueryPostgresTests._drop_schema
    _factory = query_tests.AssessmentQueryPostgresTests._factory
    _batch = query_tests.AssessmentQueryPostgresTests._batch
    _run = query_tests.AssessmentQueryPostgresTests._run

    def populate(self):
        self._run()
        self.ages = {}
        position = 0
        for number, uuid in enumerate(self.findings):
            ages = AGE_CASES[number % len(AGE_CASES)]
            self.ages[uuid] = ages
            self.connection.execute(
                "UPDATE households SET responses = responses || %s WHERE batch_id=%s AND uuid=%s",
                (Jsonb({'barangay': 'North' if number % 2 else 'South'}), self.batch_id, uuid),
            )
            for line, age in enumerate(ages, 1):
                position += 1
                self.connection.execute(
                    'INSERT INTO household_members VALUES (%s,%s,%s,%s,%s)',
                    (self.batch_id, uuid, line, position, Jsonb({
                        'a07_age': age, 'a02_relation_to_hh_head': '1' if line == 1 else '3',
                        'a01_first_name': 'Case', 'a01_last_name': str(number)})),
                )
        return {uuid: current_spisc_status(ages, {'survey_year': 2022}, ASSESSED)
                for uuid, ages in self.ages.items()}

    def test_counts_filters_paging_and_ranks_match_python(self):
        statuses = self.populate()
        snapshot = self.connection.execute(
            'SELECT household_uuid, findings, vulnerability_rank FROM household_assessments '
            'WHERE batch_id=%s ORDER BY household_uuid', (self.batch_id,),
        ).fetchall()
        for programs, mode in (((), 'any'), (('SPISC',), 'any'),
                               (('4Ps', 'SPISC'), 'any'), (('SPISC',), 'exact'),
                               (('SLP', 'SPISC'), 'exact')):
            filters = AssessmentFilters(programs=programs, subsidy_mode=mode)
            expected = []
            counts = {'all': 0, 'primary': 0, 'secondary': 0, 'both': 0, 'other': 0}
            for uuid, finding in self.findings.items():
                current = {p: f['screening_status'] for p, f in finding['programs'].items()}
                current['SPISC'] = statuses[uuid]
                signals = {p for p, status in current.items() if status == 'For Review'}
                if programs and not (signals == set(programs) if mode == 'exact'
                                     else signals.intersection(programs)):
                    continue
                priority = derive_subsidy_priority(finding['mpi']['vulnerability_score'], current)
                tier = {'Primary Priority': 'primary', 'Secondary Priority': 'secondary',
                        'No Subsidy Priority': 'other'}[priority]
                counts['all'] += 1
                counts[tier] += 1
                expected.append(uuid)
            counts['both'] = counts['primary'] + counts['secondary']
            for tier in ('all', 'primary', 'secondary', 'other', 'both'):
                selected = replace(filters, priority=tier)
                result = assessment_queries.list_active_assessment_summaries(
                    filters=selected, include_priority_counts=True, page_size=100,
                    assessment_date=ASSESSED, connection_factory=self._factory,
                )
                self.assertEqual(result['priority_counts'], counts)
                self.assertEqual(result['total'], counts[tier])
                for row in result['items']:
                    original = self.findings[row['household_uuid']]
                    self.assertEqual(row['program_statuses']['SPISC'], statuses[row['household_uuid']])
                    self.assertEqual(row['vulnerability_rank'], original['mpi']['vulnerability_rank'])
                    if tier not in ('all', 'both'):
                        self.assertEqual(row['subsidy_priority'], {
                            'primary': 'Primary Priority', 'secondary': 'Secondary Priority',
                            'other': 'No Subsidy Priority'}[tier])
            ordered = sorted(expected, key=lambda uuid: (
                self.findings[uuid]['mpi']['vulnerability_rank'], uuid))
            actual = []
            for page in range(1, (len(ordered) + 6) // 7 + 1):
                result = assessment_queries.list_active_assessment_summaries(
                    filters=filters, page=page, page_size=7, assessment_date=ASSESSED,
                    connection_factory=self._factory)
                actual.extend(row['household_uuid'] for row in result['items'])
            self.assertEqual(actual, ordered)
        self.assertEqual(snapshot, self.connection.execute(
            'SELECT household_uuid, findings, vulnerability_rank FROM household_assessments '
            'WHERE batch_id=%s ORDER BY household_uuid', (self.batch_id,),
        ).fetchall())

    def test_sql_parity_across_dates_future_contexts_and_search(self):
        self.populate()
        for context in ({'survey_year': 2022}, {'survey_year': 2024},
                        {'survey_year': 2024, 'source_provenance': {'declared': {
                            'age_reference_date': '2024-02-29', 'age_reference_date_verified': True}}}):
            for assessed in (date(2026, 1, 1), date(2026, 6, 30), date(2026, 7, 1),
                             date(2027, 12, 31), date(2028, 2, 29)):
                with self.subTest(context=context, assessed=assessed):
                    mapping = {age: current_spisc_status([age], context, assessed)
                               for age in assessment_evidence.list_recorded_ages(self.connection, self.batch_id)
                               if age is not None}
                    rows = assessment_runs.list_assessment_summaries(
                        self.connection, self.connection.execute(
                            'SELECT run_id FROM assessment_run_selections WHERE batch_id=%s',
                            (self.batch_id,)).fetchone()[0], limit=100, offset=0,
                        spisc_age_statuses=mapping, batch_id=self.batch_id)
                    self.assertEqual({r['household_uuid']: r['program_statuses']['SPISC'] for r in rows},
                                     {uuid: current_spisc_status(ages, context, assessed)
                                      for uuid, ages in self.ages.items()})
        result = assessment_queries.list_active_assessment_summaries(
            filters=AssessmentFilters(search='Case', barangay='North', programs=('SPISC',)),
            assessment_date=ASSESSED, connection_factory=self._factory)
        self.assertTrue(result['items'])
        for row in result['items']:
            self.assertEqual(row['program_statuses']['SPISC'], 'For Review')

    def test_priority_thresholds_use_current_signal(self):
        primary, first = assessment_runs._priority_condition('primary', True)
        secondary, second = assessment_runs._priority_condition('secondary', True)
        other, last = assessment_runs._priority_condition('other', True)
        rows = self.connection.execute(
            f"SELECT score, {primary}, {secondary}, {other} "
            "FROM (VALUES (19.999), (20), (25), (25.001)) scores(score) "
            "CROSS JOIN LATERAL (SELECT jsonb_build_object('mpi', "
            "jsonb_build_object('vulnerability_score',score)) AS findings) h "
            "CROSS JOIN (SELECT 'For Review' AS status) current_spisc",
            (*first, *second, *last),
        ).fetchall()
        self.assertEqual([row[1:] for row in rows], [
            (False, False, True), (False, True, False),
            (False, True, False), (True, False, False)])


@unittest.skipUnless(review_tests.TEST_URL, 'Set the isolated local test database URL.')
class CurrentSpiscReviewDatabaseTests(unittest.TestCase):
    setUp = review_tests.StaffReviewDatabaseTests.setUp
    connect = review_tests.StaffReviewDatabaseTests.connect
    drop_schema = review_tests.StaffReviewDatabaseTests.drop_schema
    make_batch = review_tests.StaffReviewDatabaseTests.make_batch
    snapshot = review_tests.StaffReviewDatabaseTests.snapshot

    def test_creation_revalidates_current_age_not_saved_status(self):
        with self.connect() as connection:
            connection.execute('INSERT INTO household_members VALUES (%s,%s,1,1,%s)',
                               (self.batch_id, review_tests.HOUSEHOLD_UUID, Jsonb({'a07_age': '55'})))
        # Historical result is negative, yet current possible senior needs no reason.
        with patch.object(review_tests.service, 'philippine_assessment_date', return_value=ASSESSED):
            review_id = review_tests.service.start_staff_review(
                review_tests.HOUSEHOLD_UUID, 'SPISC', '', self.staff_ids[0],
                self.batch_id, self.run_id, connection_factory=self.connect)
            with self.assertRaisesRegex(review_tests.service.StaffReviewError, 'already_active'):
                review_tests.service.start_staff_review(
                    review_tests.HOUSEHOLD_UUID, 'SPISC', '', self.staff_ids[0],
                    self.batch_id, self.run_id, connection_factory=self.connect)
        with self.connect() as connection:
            row = connection.execute('SELECT started_by, starting_reason FROM staff_reviews WHERE id=%s',
                                     (review_id,)).fetchone()
            self.assertEqual(row, (self.staff_ids[0], None))
            self.assertEqual(self.originals['household_assessments'], self.snapshot(connection)['household_assessments'])

    def test_missing_empty_and_younger_ages_require_reason(self):
        for ages in ([], [None], ['30']):
            with self.subTest(ages=ages), self.connect() as connection:
                connection.execute('DELETE FROM household_members WHERE batch_id=%s', (self.batch_id,))
                for line, age in enumerate(ages, 1):
                    connection.execute('INSERT INTO household_members VALUES (%s,%s,%s,%s,%s)',
                                       (self.batch_id, review_tests.HOUSEHOLD_UUID, line, line,
                                        Jsonb({'a07_age': age})))
            with self.assertRaisesRegex(review_tests.service.StaffReviewError, 'reason_required'):
                review_tests.service.start_staff_review(
                    review_tests.HOUSEHOLD_UUID, 'SPISC', '', self.staff_ids[0],
                    self.batch_id, self.run_id, assessment_date=ASSESSED,
                    connection_factory=self.connect)

    def test_direct_posts_confirmation_and_existing_review_safeguards(self):
        app = create_app({'TESTING': True, 'SECRET_KEY': 'synthetic-key-' * 5,
                          'DATABASE_URL': self.url})
        client = app.test_client()
        with client.session_transaction() as session:
            from time import time
            session.update(user_id=self.staff_ids[0], session_version=1,
                           issued_at=int(time()), csrf_token='synthetic-token')
        path = f'/assessments/{review_tests.HOUSEHOLD_UUID}/reviews/SPISC'
        data = {'csrf_token': 'synthetic-token', 'batch_id': str(self.batch_id),
                'run_id': str(self.run_id), 'screening_status': 'For Review',
                'requires_reason': 'false', 'staff_id': str(self.staff_ids[1])}
        with self.connect() as connection:
            connection.execute('INSERT INTO household_members VALUES (%s,%s,1,1,%s)',
                               (self.batch_id, review_tests.HOUSEHOLD_UUID,
                                Jsonb({'a07_age': '30', 'a02_relation_to_hh_head': '1'})))
        with patch.object(review_tests.service, 'philippine_assessment_date', return_value=ASSESSED), \
                patch('services.subsidy_screening_details.philippine_assessment_date', return_value=ASSESSED):
            self.assertEqual(client.post(path, data=data).status_code, 400)
            self.assertEqual(client.post(path, data={**data, 'csrf_token': 'tampered'}).status_code, 400)
            with self.connect() as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 0)
                connection.execute("UPDATE household_members SET responses = responses || %s WHERE batch_id=%s",
                                   (Jsonb({'a07_age': '55'}), self.batch_id))
                baseline = self.snapshot(connection)
            confirmation = client.get(path)
            self.assertEqual(confirmation.status_code, 200)
            self.assertIn('Current SPISC recommendation', confirmation.text)
            self.assertIn('No Review Signal', confirmation.text)
            self.assertIn('#screening-spisc', confirmation.text)
            self.assertNotIn('name="starting_reason"', confirmation.text)
            self.assertNotIn('class="spisc-findings"', confirmation.text)
            result = client.post(path + '?page=2&priority=secondary', data=data)
            self.assertEqual(result.status_code, 303)
            self.assertIn('page=2', result.location)
            self.assertEqual(client.post(path, data=data).status_code, 303)
            with self.connect() as connection:
                rows = connection.execute('SELECT id, started_by FROM staff_reviews').fetchall()
                self.assertEqual(len(rows), 1)
                review_id = rows[0][0]
                self.assertEqual(rows[0][1], self.staff_ids[0])
            workspace = client.get(path + '/' + str(review_id))
            self.assertEqual(workspace.status_code, 200)
            self.assertIn('Current SPISC recommendation', workspace.text)
            self.assertIn('Historical CBMS 2022 screening', workspace.text)
            self.assertIn('#screening-spisc', workspace.text)
            self.assertNotIn('class="spisc-findings"', workspace.text)
            review_tests.service.act_on_review(
                review_tests.HOUSEHOLD_UUID, 'SPISC', review_id, 'finish', self.staff_ids[0],
                self.batch_id, self.run_id, text='Synthetic follow-up.',
                recommendation='For LGU Follow-Up', connection_factory=self.connect)
            self.assertEqual(client.post(path, data=data).status_code, 303)
            with self.connect() as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 1)
            change_recommendation(
                review_tests.HOUSEHOLD_UUID, 'SPISC', review_id, 'withdraw', self.staff_ids[0],
                self.batch_id, self.run_id, version=0, reason='Synthetic withdrawal.',
                connection_factory=self.connect)
            self.assertEqual(client.post(path, data=data).status_code, 303)
            with self.connect() as connection:
                new_id = connection.execute('SELECT max(id) FROM staff_reviews').fetchone()[0]
            review_tests.service.act_on_review(
                review_tests.HOUSEHOLD_UUID, 'SPISC', new_id, 'cancel', self.staff_ids[0],
                self.batch_id, self.run_id, text='Synthetic cancellation.', connection_factory=self.connect)
            self.assertEqual(client.post(path, data=data).status_code, 303)
            with self.connect() as connection:
                self.assertEqual(self.snapshot(connection), baseline)
                self.assertEqual(connection.execute(
                    'SELECT closing_recommendation FROM staff_reviews WHERE id=%s',
                    (review_id,)).fetchone()[0], 'For LGU Follow-Up')
