"""Queue verification uses synthetic records and the guarded disposable test schema."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import unescape
import re
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from psycopg.types.json import Jsonb
from werkzeug.datastructures import MultiDict

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID
from repositories import assessment_runs, staff_reviews
from services import review_queue as queue
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import AssessmentQueryError
import test_staff_reviews as reviews


def queue_fixture(page=1, filters=None, *, total=51, staff_id=None):
    """Exercise actual presentation with mocked query boundaries and stored rows."""
    filters = filters or queue.QueueFilters()
    now = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
    row = {
        'id': 42, 'household_uuid': HOUSEHOLD_UUID, 'program': '4Ps',
        'vulnerability_rank': 7, 'created_at': now, 'finished_at': now,
        'status': 'In Review' if filters.status == 'in-review' else 'Closed',
        'closing_recommendation': dict(queue.STATUS_OPTIONS)[filters.status],
        'responsible_staff_id': 7, 'responsible_username': 'synthetic_staff',
    }
    identity = {
        'household_uuid': HOUSEHOLD_UUID, 'source_row_number': 8, 'head_count': 1,
        'first_name': 'Synthetic', 'middle_name': '', 'last_name': 'Head',
        'barangay': 'Synthetic Barangay',
    }
    context = {'batch_id': 1, 'run_id': 2, 'survey_year': 2022, 'municipality': 'Pozorrubio'}
    with (
        patch.object(queue, '_read_context', return_value=nullcontext((MagicMock(), context))),
        patch.object(staff_reviews, 'count_queue', return_value=total),
        patch.object(staff_reviews, 'list_queue', return_value=[row] if total else []),
        patch.object(queue.dataset_batches, 'find_household_identities',
                     return_value=[identity] if total else []),
        patch.object(queue.dataset_batches, 'list_barangays', return_value=['Synthetic Barangay']),
    ):
        return queue.get_review_queue_page(page, filters, staff_id=staff_id or 7)


class QueueFilterTests(unittest.TestCase):
    def test_canonical_filter_and_return_round_trip(self):
        page, filters = queue.parse_queue_arguments(MultiDict([
            ('status', 'follow-up'), ('page', '2'), ('q', ' Synthetic   Head '),
            ('barangay', ' Synthetic Barangay '), ('program', 'TUPAD'), ('program', '4Ps'),
        ]))
        self.assertEqual(page, 2)
        self.assertEqual(filters.programs, ('4Ps', 'TUPAD'))
        self.assertEqual(filters.search, 'Synthetic Head')
        parameters = queue.queue_return_parameters(page, filters)
        arguments = MultiDict(parse_qs(urlencode(parameters, doseq=True)))
        self.assertEqual(queue.parse_queue_return(arguments), (page, filters))
        self.assertNotIn('q', parameters)
        self.assertNotIn('program', parameters)

    def test_invalid_filters_and_return_state_fail_before_queries(self):
        for query in (
            'status=Closed', 'status=in-review&status=no-action', 'page=0', 'page=-1',
            'page=1&page=2', 'page=9999999999', 'page=١', 'program=UNKNOWN',
            'barangay=%00', 'q=' + 'x' * 201, 'barangay=' + 'x' * 201,
            'program=4Ps&program=4Ps&program=4Ps&program=4Ps&program=4Ps',
        ):
            with self.subTest(query=query), self.assertRaises(InvalidAssessmentFilters):
                queue.parse_queue_arguments(MultiDict(parse_qs(query, keep_blank_values=True)))
        for query in (
            'from_queue=https://example.com', 'from_queue=1&from_queue=1',
            'from_queue=1&queue_url=https://example.com', 'queue_page=2',
            'from_queue=1&queue_status=Cancelled', 'from_queue=1&queue_page=0',
        ):
            with self.subTest(query=query), self.assertRaises(InvalidAssessmentFilters):
                queue.parse_queue_return(MultiDict(parse_qs(query)))
        connect = MagicMock()
        with self.assertRaises(InvalidAssessmentFilters):
            queue.get_review_queue_page(1, queue.QueueFilters(status='Closed'),
                                        connection_factory=connect)
        connect.assert_not_called()

    def test_preparation_keeps_original_rank_and_bounds_pages(self):
        result = queue_fixture(2)
        self.assertEqual(result['items'][0]['vulnerability_rank'], 7)
        self.assertEqual(result['items'][0]['household_reference_id'], 'HH-00008')
        self.assertEqual(result['items'][0]['head_name'], 'Synthetic Head')
        self.assertEqual(result['page_count'], 3)
        self.assertEqual(result['first_record'], 26)
        self.assertIsNone(queue_fixture(4))


class QueueRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reviews.StaffReviewRouteTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client = self.fixture.client
        patcher = patch('routes.assessments.get_review_queue_page', side_effect=queue_fixture)
        self.query = patcher.start()
        self.addCleanup(patcher.stop)

    def test_authorization_status_tabs_and_exact_table_columns(self):
        self.assertEqual(self.client.get('/assessments/queue').location, '/login')
        self.query.assert_not_called()
        for role in ('lgu_staff', 'system_administrator'):
            self.fixture.user['role'] = role
            self.fixture.session()
            page = self.client.get('/assessments/queue')
            if role == 'system_administrator':
                self.assertEqual(page.status_code, 403)
                continue
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers['Cache-Control'], 'no-store')
            self.assertIn('Open Review', page.text)
            self.assertNotIn('subsidy_mode', page.text)
            self.assertNotIn('Planned', page.text.split('<main', 1)[1])
            headings = re.findall(r'<th scope="col"[^>]*>(.*?)</th>', page.text)
            self.assertEqual(headings, [
                'Municipal Rank', 'Household ID', 'Household Head', 'Barangay',
                'Subsidy', 'Staff Review', 'Date Started', 'Action',
            ])
            status_nav = re.search(r'<nav[^>]*aria-label="Review status".*?</nav>',
                                   page.text, re.S)[0]
            self.assertEqual(len(re.findall(r'<a\s', status_nav)), 3)
            self.assertEqual(status_nav.count('aria-current="page"'), 1)
        self.fixture.user['role'] = 'citizen'
        self.fixture.session()
        self.assertEqual(self.client.get('/assessments/queue').location, '/login')

    def test_completed_tabs_filters_pagination_and_empty_states(self):
        self.fixture.session()
        for status, label in queue.STATUS_OPTIONS[1:]:
            page = self.client.get('/assessments/queue?' + urlencode({
                'status': status, 'page': 2, 'q': 'Synthetic',
                'barangay': 'Synthetic Barangay', 'program': ['4Ps', 'SLP'],
                'scope': 'mine', 'sort': 'asc',
            }, doseq=True))
            self.assertEqual(page.status_code, 200)
            self.assertIn(label, page.text)
            self.assertIn('Date Finished', page.text)
            self.assertIn('View Review', page.text)
            self.assertNotIn('Open Review', page.text)
            hrefs = [unescape(url) for url in re.findall(r'href="([^"]+)"', page.text)]
            record = next(url for url in hrefs if '/reviews/4Ps/42' in url)
            arguments = parse_qs(urlsplit(record).query)
            self.assertEqual(arguments['queue_page'], ['2'])
            self.assertEqual(arguments['queue_status'], [status])
            self.assertEqual(arguments['queue_program'], ['4Ps', 'SLP'])
            self.assertEqual(arguments['queue_scope'], ['mine'])
            self.assertEqual(arguments['queue_sort'], ['asc'])
            pagination = re.search(r'aria-label="Review queue pages".*?</nav>', page.text, re.S)[0]
            self.assertIn('scope=mine', pagination)
            self.assertIn('sort=asc', pagination)
            tab_links = [url for url in hrefs if '/queue?status=' in url and 'page=' not in url]
            self.assertGreaterEqual(len(tab_links), 3)
        self.query.side_effect = lambda page, filters, **kwargs: queue_fixture(page, filters, total=0)
        page = self.client.get('/assessments/queue?scope=all')
        self.assertIn('No reviews are currently in progress.', page.text)
        page = self.client.get('/assessments/queue?q=missing')
        self.assertIn('No reviews match your search or filters.', page.text)
        self.query.return_value = None
        self.query.side_effect = None
        response = self.client.get('/assessments/queue?page=2&program=SLP')
        self.assertEqual(response.status_code, 303)
        self.assertIn('program=SLP', response.location)
        self.assertIn('page=1', response.location)

    def test_visible_scope_control_uses_role_defaults_and_respects_explicit_choices(self):
        self.fixture.session()
        for role, default in (('lgu_staff', 'mine'), ('system_administrator', 'all')):
            self.fixture.user['role'] = role
            for choice in (None, 'mine', 'all'):
                with self.subTest(role=role, choice=choice):
                    response = self.client.get('/assessments/queue' + (
                        '' if choice is None else '?scope=' + choice
                    ))
                    if role == 'system_administrator':
                        self.assertEqual(response.status_code, 403)
                        continue
                    self.assertEqual(response.status_code, 200)
                    scope = choice or default
                    self.assertEqual(self.query.call_args.args[1].scope, scope)
                    control = re.search(r'<nav[^>]*aria-label="Review assignment".*?</nav>',
                                        response.text, re.S)[0]
                    links = re.findall(r'<a\s.*?</a>', control, re.S)
                    self.assertEqual(len(links), 2)
                    self.assertIn('Assigned to Me', links[0])
                    self.assertIn('All Staff Reviews', links[1])
                    selected = next(link for link in links if 'aria-current="page"' in link)
                    self.assertIn('scope=' + scope, selected)
                    self.assertNotIn('id="queue-scope"', response.text)
                    self.assertIn(f'name="scope" value="{scope}"', response.text)
                    expected = ('Reviews assigned to you as the responsible staff member.'
                                if scope == 'mine' else 'Reviews assigned to authorized LGU personnel.')
                    self.assertIn(expected, response.text)
                    self.assertLess(response.text.index('queue-status-guide'),
                                    response.text.index('queue-scope-switch'))
                    for meaning in queue.STATUS_EXPLANATIONS.values():
                        self.assertIn(meaning, response.text)
                    self.assertIn('Staff recommendations do not mean subsidy approval '
                                  'or denial of eligibility.', response.text)
                    self.assertLess(response.text.index('queue-scope-switch'),
                                    response.text.index('Filter staff reviews'))
        self.fixture.user['role'] = 'lgu_staff'
        for bad in ('scope=', 'scope=unknown', 'scope=mine&scope=all',
                    'scope=https://example.com'):
            self.query.reset_mock()
            self.assertEqual(self.client.get('/assessments/queue?' + bad).status_code, 400)
            self.query.assert_not_called()

    def test_scope_switch_and_clear_filters_preserve_selected_context(self):
        self.fixture.session()
        for scope in ('mine', 'all'):
            parameters = {'status': 'follow-up', 'page': 2, 'scope': scope, 'q': 'Synthetic',
                          'barangay': 'Synthetic Barangay', 'program': ['4Ps', 'SLP'], 'sort': 'asc'}
            page = self.client.get('/assessments/queue?' + urlencode(parameters, doseq=True))
            control = re.search(r'<nav[^>]*aria-label="Review assignment".*?</nav>', page.text, re.S)[0]
            for href in re.findall(r'href="([^"]+)"', control):
                parsed = parse_qs(urlsplit(unescape(href)).query)
                self.assertNotIn('page', parsed)
                for key in ('status', 'q', 'barangay', 'program', 'sort'):
                    value = parameters[key]
                    self.assertEqual(parsed[key], value if isinstance(value, list) else [value])
            clear = unescape(re.search(r'class="clear-filters" href="([^"]+)"', page.text)[1])
            self.assertEqual(parse_qs(urlsplit(clear).query), {'status': ['follow-up'], 'scope': [scope]})
            self.client.get(clear)
            self.assertEqual(self.query.call_args.args[1].scope, scope)
            # Apply Filters submits the hidden selection and resets pagination.
            self.client.get('/assessments/queue?' + urlencode({**parameters, 'page': 1}, doseq=True))
            self.assertEqual(self.query.call_args.args[1].scope, scope)

    def test_personal_empty_state_is_distinct_from_filtered_and_shared_lists(self):
        self.fixture.session()
        self.query.side_effect = lambda page, filters, **kwargs: queue_fixture(page, filters, total=0)
        for status, _ in queue.STATUS_OPTIONS:
            empty = self.client.get('/assessments/queue?status=' + status)
            self.assertIn('No reviews are currently assigned to you.' if status == 'in-review'
                          else 'No reviews assigned to you have this recommendation yet.', empty.text)
            self.assertIn('View All Staff Reviews', empty.text)
            filtered = self.client.get('/assessments/queue?status=' + status + '&q=missing')
            self.assertIn('No reviews match your search or filters.', filtered.text)
            self.assertNotIn('No reviews are currently assigned to you.', filtered.text)

    def test_safe_return_state_survives_assessment_links_and_review_forms(self):
        self.fixture.session()
        filters = queue.QueueFilters('in-review', 'Synthetic', 'Synthetic Barangay',
                                     ('4Ps', 'SLP'), 'mine', 'desc')
        parameters = queue.queue_return_parameters(2, filters)
        query = urlencode(parameters, doseq=True)
        self.fixture.source['staff_reviews'] = [{
            'id': 42, 'current_review_id': 42, 'program': '4Ps', 'status': 'In Review', 'username': 'synthetic_staff',
            'created_at': datetime(2026, 10, 17, tzinfo=timezone.utc), 'notes': [],
            'starting_reason': '', 'closing_recommendation': None,
        }]
        record = self.fixture.path + '/reviews/4Ps/42'
        page = self.client.get(record + '?' + query)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Back to Review Queue', page.text)
        self.assertIn('View Household Assessment', page.text)
        back = unescape(re.search(r'<a class="review-return-link context-navigation" href="([^"]+)"',
                                  page.text, re.S)[1])
        self.assertTrue(back.startswith('/assessments/queue?'))
        self.assertEqual(parse_qs(urlsplit(back).query)['scope'], ['mine'])
        self.assertEqual(parse_qs(urlsplit(back).query)['sort'], ['desc'])
        self.client.get(back)
        self.assertEqual(self.query.call_args.args[1], filters)
        links = [unescape(url) for url in re.findall(r'href="([^"]+)"', page.text)]
        finish = next(url for url in links if 'action=finish' in url)
        self.assertEqual(parse_qs(urlsplit(finish).query)['queue_page'], ['2'])
        for tab in ('overview', 'mpi', 'screening', 'reviews'):
            page = self.client.get(self.fixture.path + '?tab=' + tab + '&' + query)
            self.assertEqual(page.status_code, 200)
            self.assertIn('Back to Review Queue', page.text)
            self.assertIn('queue_program=4Ps', page.text)
            self.assertNotIn('priority=primary', page.text)
        with patch('routes.assessments.act_on_review') as action:
            response = self.client.post(record + '?' + query, data={
                'csrf_token': 'test-token', 'action': 'note', 'text': 'Synthetic note.',
                'batch_id': 1, 'run_id': 2,
            })
        self.assertEqual(response.status_code, 303)
        self.assertEqual(parse_qs(urlsplit(response.location).query)['queue_page'], ['2'])
        action.assert_called_once()
        for bad in ('from_queue=1&queue_status=Closed', 'from_queue=1&queue_url=https://evil.test'):
            self.assertEqual(self.client.get(record + '?' + bad).status_code, 400)
        self.query.side_effect = AssessmentQueryError('private detail')
        response = self.client.get('/assessments/queue')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private detail', response.text)


@unittest.skipUnless(reviews.TEST_URL, 'Set the isolated local test database URL.')
class QueueDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reviews.StaffReviewDatabaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connect = self.fixture.connect

    def page(self, status='in-review', **filters):
        return queue.get_review_queue_page(1, queue.QueueFilters(status=status, **filters),
                                           connection_factory=self.connect)

    def test_latest_states_batch_scope_designated_rank_and_source_preservation(self):
        fx = self.fixture
        old = fx.start()
        fx.act(old, 'finish', recommendation='For LGU Follow-Up', text='Further consideration.')
        active = fx.legacy_start()
        no_action = fx.start('SLP', 'Staff request.')
        self.assertEqual({r['id'] for r in self.page()['items']}, {active, no_action})
        fx.act(no_action, 'finish', program='SLP', recommendation='No Further Action', text='Examined.')
        previous = fx.start('TUPAD')
        fx.act(previous, 'finish', program='TUPAD', recommendation='For LGU Follow-Up', text='Examined.')
        cancelled = fx.legacy_start('TUPAD')
        fx.act(cancelled, 'cancel', program='TUPAD', text='Started in error.')
        with self.connect() as connection:
            staff_reviews.insert_active(connection, batch_id=fx.other_batch,
                                        household_uuid=HOUSEHOLD_UUID, run_id=fx.other_run,
                                        program='SPISC', starting_reason='Other batch.',
                                        staff_id=fx.staff_ids[0])
        self.assertEqual([r['id'] for r in self.page()['items']], [active])
        self.assertEqual([r['id'] for r in self.page('no-action')['items']], [no_action])
        self.assertEqual(self.page('follow-up')['total'], 1)
        with self.connect() as connection:
            self.assertEqual(fx.snapshot(connection), fx.originals)
            self.assertEqual(len(staff_reviews.list_history(connection, fx.batch_id, HOUSEHOLD_UUID)), 5)
            findings = deepcopy(fx.source['findings'])
            findings['mpi']['vulnerability_rank'] = 99
            new_run = assessment_runs.create_run(connection, fx.batch_id,
                                                calculation_version='synthetic',
                                                calculation_provenance={'kind': 'synthetic test'})
            assessment_runs.store_household_assessments(connection, new_run, {HOUSEHOLD_UUID: findings})
            assessment_runs.complete_run(connection, new_run)
            assessment_runs.designate_run(connection, fx.batch_id, new_run)
        self.assertEqual(self.page()['items'][0]['vulnerability_rank'], 99)
        self.assertEqual(self.page()['items'][0]['id'], active)
        with self.connect() as connection:
            connection.execute('DELETE FROM assessment_run_selections WHERE batch_id=%s', (fx.batch_id,))
        with self.assertRaisesRegex(AssessmentQueryError, 'approved_assessment_run_missing'):
            self.page()

    def test_query_filtering_order_ties_and_pagination_with_stored_ranks(self):
        fx = self.fixture
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ids = []
        with self.connect() as connection:
            original = connection.execute('SELECT findings FROM household_assessments WHERE run_id=%s',
                                          (fx.run_id,)).fetchone()[0]
            for index in range(30):
                household = f'00000000-0000-4000-8000-{index + 100:012d}'
                connection.execute('INSERT INTO households VALUES (%s, %s, %s, %s)',
                                   (fx.batch_id, household, index + 100, Jsonb({'barangay': 'Queue A' if index % 2 else 'Queue B'})))
                connection.execute(
                    'INSERT INTO household_members (batch_id, uuid, line_number, source_row_number, responses) VALUES (%s, %s, 1, %s, %s)',
                    (fx.batch_id, household, index + 100, Jsonb({
                        'a02_relation_to_hh_head': '1', 'a01_first_name': 'Queue%_!' if index == 0 else 'Queue',
                        'a01_last_name': f'Head {index}',
                    })),
                )
                findings = deepcopy(original)
                findings.update(household_uuid=household)
                findings['mpi'].update(uuid=household, vulnerability_rank=500 - index)
                connection.execute('INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings) VALUES (%s, %s, %s, %s)',
                                   (fx.run_id, fx.batch_id, household, Jsonb(findings)))
                review_id = staff_reviews.insert_active(
                    connection, batch_id=fx.batch_id, household_uuid=household, run_id=fx.run_id,
                    program='SLP' if index % 2 else '4Ps', starting_reason='Synthetic request.', staff_id=fx.staff_ids[0],
                )
                ids.append(review_id)
                connection.execute('UPDATE staff_reviews SET created_at=%s WHERE id=%s',
                                   (started + timedelta(days=index // 2), review_id))
        first = self.page()
        self.assertEqual(first['total'], 30)
        self.assertEqual([r['id'] for r in first['items']], ids[:25])
        self.assertEqual(first['items'][0]['vulnerability_rank'], 500)
        second = queue.get_review_queue_page(2, connection_factory=self.connect)
        self.assertEqual([r['id'] for r in second['items']], ids[25:])
        self.assertEqual(self.page(search='HH-00100')['items'][0]['vulnerability_rank'], 500)
        self.assertEqual(self.page(search='%_!')['total'], 1)
        self.assertEqual(self.page(search="' OR 1=1 --")['total'], 0)
        self.assertEqual(self.page(search='Queue', barangay='Queue A', programs=('4Ps', 'SLP'))['total'], 15)
        self.assertEqual(self.page(programs=('TUPAD',))['total'], 0)
        with self.connect() as connection:
            for index, review_id in enumerate(ids[:3]):
                staff_reviews.finish(connection, review_id, fx.staff_ids[0], 'For LGU Follow-Up', 'Examined.')
                connection.execute('UPDATE staff_reviews SET finished_at=%s WHERE id=%s',
                                   (started + timedelta(days=index // 2), review_id))
        self.assertEqual([r['id'] for r in self.page('follow-up')['items']], list(reversed(ids[:3])))

    def test_http_queue_finish_and_cancel_round_trip_keeps_context_and_history(self):
        fx = self.fixture
        review_id = fx.start()
        client = create_app({'TESTING': True, 'SECRET_KEY': 'synthetic-secret-' * 4,
                             'DATABASE_URL': fx.url}).test_client()
        path = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps/{review_id}'
        state = urlencode(queue.queue_return_parameters(1, queue.QueueFilters(programs=('4Ps',))), doseq=True)
        for staff_id in fx.staff_ids:
            with client.session_transaction() as session:
                session.update(user_id=staff_id, session_version=1, issued_at=int(reviews.time()),
                               csrf_token='test-token')
            expected = 403 if staff_id == fx.staff_ids[0] else 200
            self.assertEqual(client.get('/assessments/queue').status_code, expected)
        response = client.post(path + '?' + state + '&action=finish', data={
            'csrf_token': 'test-token', 'action': 'finish', 'batch_id': fx.batch_id,
            'run_id': fx.run_id, 'recommendation': 'For LGU Follow-Up', 'text': 'Further consideration.',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Find this record under For LGU Follow-Up in the Review Queue.', response.text)
        self.assertIn('Back to Review Queue', response.text)
        self.assertNotIn('id="review-note"', response.text)
        self.assertEqual(self.page()['total'], 0)
        self.assertEqual(self.page('follow-up')['total'], 1)
        second = fx.legacy_start()
        response = client.post(path.rsplit('/', 1)[0] + '/' + str(second) + '?' + state + '&action=cancel', data={
            'csrf_token': 'test-token', 'action': 'cancel', 'batch_id': fx.batch_id,
            'run_id': fx.run_id, 'text': 'Started in error.',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Back to Review Queue', response.text)
        self.assertIn('Review cancelled. No recommendation was made.', response.text)
        self.assertEqual(self.page('follow-up')['total'], 1)
        with self.connect() as connection:
            self.assertEqual(fx.snapshot(connection), fx.originals)
            self.assertEqual(len(staff_reviews.list_history(connection, fx.batch_id, HOUSEHOLD_UUID)), 2)
