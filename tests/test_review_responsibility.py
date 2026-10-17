"""Responsibility, stale forms and shared access on isolated synthetic records."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
import re
from threading import Barrier, Event
from time import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from werkzeug.datastructures import MultiDict

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID
from repositories import staff_reviews as repository
from services import review_queue as queue, staff_reviews as service
from services.assessment_filters import InvalidAssessmentFilters
import test_staff_reviews as reviews


class ResponsibilityFilterTests(unittest.TestCase):
    def test_scope_sort_validation_and_complete_return_round_trip(self):
        filters = queue.QueueFilters('follow-up', 'Synthetic', 'West', ('SLP',), 'mine', 'asc')
        arguments = MultiDict(parse_qs(urlencode(queue.queue_return_parameters(2, filters), doseq=True)))
        self.assertEqual(queue.parse_queue_return(arguments), (2, filters))
        self.assertEqual(queue.QueueFilters().date_order, 'asc')
        self.assertEqual(queue.QueueFilters(status='no-action').date_order, 'desc')
        for query in ('scope=other', 'scope=all&scope=mine', 'sort=ASC', 'sort=asc&sort=desc',
                      'from_queue=1&queue_scope=other', 'from_queue=1&queue_sort=invalid'):
            with self.subTest(query=query), self.assertRaises(InvalidAssessmentFilters):
                args = MultiDict(parse_qs(query))
                (queue.parse_queue_return if 'from_queue' in args else queue.parse_queue_arguments)(args)
        with self.assertRaises(InvalidAssessmentFilters):
            queue.get_review_queue_page(1, queue.QueueFilters(scope='mine'),
                                        connection_factory=lambda: self.fail('Opened connection'))


@unittest.skipUnless(reviews.TEST_URL, 'Set the isolated local test database URL.')
class ResponsibilityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = reviews.StaffReviewDatabaseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connect = self.fixture.connect
        self.admin, self.staff = self.fixture.staff_ids

    def change(self, review_id, target, version=1, **values):
        return self.fixture.act(review_id, 'reassign', responsible_staff_id=target,
                                responsibility_version=version,
                                text=values.pop('text', 'Covering this review.'), **values)

    def history(self):
        with self.connect() as connection:
            return repository.list_history(connection, self.fixture.batch_id, HOUSEHOLD_UUID)

    def page(self, status='in-review', *, staff_id=None, **values):
        return queue.get_review_queue_page(
            1, queue.QueueFilters(status=status, **values), staff_id=staff_id,
            connection_factory=self.connect,
        )

    def client(self, staff_id=None):
        client = create_app({'TESTING': True, 'SECRET_KEY': 'synthetic-test-secret-' * 3,
                             'DATABASE_URL': self.fixture.url}).test_client()
        with client.session_transaction() as session:
            session.update(user_id=staff_id or self.staff, session_version=1, issued_at=int(time()),
                           csrf_token='test-token')
        return client

    def test_automatic_assignment_per_program_and_attributed_shared_actions(self):
        fx = self.fixture
        first = fx.start(staff_id=self.admin)
        second = fx.start('SLP', 'Examine evidence.', staff_id=self.staff)
        self.assertEqual([r['responsible_staff_id'] for r in self.history()], [self.admin, self.staff])
        fx.act(first, 'note', text='Another staff member can help.')
        self.change(first, self.staff)
        row = self.history()[0]
        self.assertEqual((row['started_by'], row['responsible_staff_id'], row['responsibility_version']),
                         (self.admin, self.staff, 2))
        change = row['reassignments'][0]
        self.assertEqual((change['previous_staff_id'], change['responsible_staff_id'], change['changed_by']),
                         (self.admin, self.staff, self.staff))
        self.assertEqual(change['reason'], 'Covering this review.')
        self.assertIsNotNone(change['created_at'])
        self.assertEqual(row['notes'][0]['authored_by'], self.staff)
        fx.act(first, 'finish', staff_id=self.admin, recommendation='No Further Action', text='Examined.')
        fx.act(second, 'cancel', program='SLP', staff_id=self.admin)
        with self.connect() as connection:
            self.assertEqual(fx.snapshot(connection), fx.originals)
        for review_id, program in ((first, '4Ps'), (second, 'SLP')):
            with self.assertRaisesRegex(service.StaffReviewError, 'not_active'):
                self.change(review_id, self.admin, 2, program=program)

    def test_invalid_inactive_unauthorized_and_unchanged_targets_leave_no_audit(self):
        review_id = self.fixture.start()
        for target, values, error in (
            (self.admin, {}, 'same_assignee'), (999999, {}, 'invalid_assignee'),
            (self.staff, {'text': '  '}, 'text_required'),
            (self.staff, {'text': 'x' * 1001}, 'invalid_text'),
            (self.staff, {'text': '\x00'}, 'invalid_text'),
            (self.staff, {'staff_id': 999999}, 'unauthorized'),
            (True, {}, 'invalid_assignee'),
        ):
            with self.subTest(target=target, values=values), self.assertRaisesRegex(service.StaffReviewError, error):
                self.change(review_id, target, **values)
        with self.connect() as connection:
            connection.execute('UPDATE staff_users SET is_active=false WHERE id=%s', (self.staff,))
        with self.assertRaisesRegex(service.StaffReviewError, 'invalid_assignee'):
            self.change(review_id, self.staff, staff_id=self.admin)
        # Simulate a future unknown role to verify the service fails closed too.
        with self.connect() as connection:
            connection.execute('ALTER TABLE staff_users DROP CONSTRAINT staff_users_role_check')
            connection.execute("UPDATE staff_users SET is_active=true, role='observer' WHERE id=%s", (self.staff,))
        with self.assertRaisesRegex(service.StaffReviewError, 'invalid_assignee'):
            self.change(review_id, self.staff, staff_id=self.admin)
        self.assertEqual(self.history()[0]['reassignments'], [])
        self.assertEqual(self.history()[0]['responsibility_version'], 1)

    def test_concurrent_and_stale_forms_cannot_overwrite_responsibility(self):
        review_id = self.fixture.start()
        barrier = Barrier(2)
        lock = repository.lock_review

        def simultaneous_lock(*args):
            barrier.wait(timeout=10)
            return lock(*args)

        def attempt():
            try:
                self.change(review_id, self.staff)
                return 'saved'
            except service.StaffReviewError as error:
                return str(error)

        with patch.object(repository, 'lock_review', side_effect=simultaneous_lock):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ['saved', 'assignment_changed'])
        self.change(review_id, self.admin, 2)
        # A->B->A must still invalidate the original form.
        with self.assertRaisesRegex(service.StaffReviewError, 'assignment_changed'):
            self.change(review_id, self.staff)
        self.assertEqual(len(self.history()[0]['reassignments']), 2)

    def test_finish_wins_row_lock_and_assignment_rolls_back_with_audit(self):
        review_id = self.fixture.start()
        attempted = Event()
        lock = repository.lock_review

        def signal_lock(*args):
            attempted.set()
            return lock(*args)

        def attempt():
            try:
                self.change(review_id, self.staff)
            except service.StaffReviewError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect() as connection:
                lock(connection, review_id, self.fixture.batch_id, HOUSEHOLD_UUID, '4Ps')
                with patch.object(repository, 'lock_review', side_effect=signal_lock):
                    future = pool.submit(attempt)
                    self.assertTrue(attempted.wait(10))
                    repository.finish(connection, review_id, self.admin, 'No Further Action', 'Examined.')
                    connection.commit()
                    self.assertEqual(future.result(timeout=10), 'not_active')
        second = self.fixture.legacy_start()
        original = repository.reassign

        def failing(*args):
            original(*args)
            raise RuntimeError('Synthetic failure after both writes')

        with patch.object(repository, 'reassign', side_effect=failing):
            with self.assertRaisesRegex(service.StaffReviewError, 'unavailable'):
                self.change(second, self.staff)
        self.assertEqual(self.history()[1]['responsibility_version'], 1)
        self.assertEqual(self.history()[1]['reassignments'], [])

    def test_assigned_filter_tracks_responsibility_all_statuses_and_date_directions(self):
        fx = self.fixture
        ids = [fx.start(), fx.start('SLP', 'Examine.', staff_id=self.staff), fx.start('TUPAD')]
        started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.connect() as connection:
            for index, review_id in enumerate(ids):
                connection.execute('UPDATE staff_reviews SET created_at=%s WHERE id=%s',
                                   (started + timedelta(days=index), review_id))
        self.assertEqual([r['id'] for r in self.page()['items']], ids)
        self.assertEqual([r['id'] for r in self.page(sort='desc')['items']], ids[::-1])
        self.assertEqual(self.page(scope='mine', staff_id=self.staff)['total'], 1)
        self.change(ids[0], self.staff)
        self.assertEqual(self.page(scope='mine', staff_id=self.staff)['total'], 2)
        self.assertEqual(self.page(scope='mine', staff_id=self.admin)['items'][0]['id'], ids[2])
        self.assertEqual(self.page(scope='mine', staff_id=self.staff, search='HH-00008',
                                   programs=('4Ps', 'SLP'))['total'], 2)
        for index, review_id in enumerate(ids):
            program = ('4Ps', 'SLP', 'TUPAD')[index]
            fx.act(review_id, 'finish', program=program, staff_id=self.admin,
                   recommendation='For LGU Follow-Up', text='Examined.')
            with self.connect() as connection:
                connection.execute('UPDATE staff_reviews SET finished_at=%s WHERE id=%s',
                                   (started + timedelta(days=index), review_id))
        self.assertEqual([r['id'] for r in self.page('follow-up')['items']], ids[::-1])
        self.assertEqual([r['id'] for r in self.page('follow-up', sort='asc')['items']], ids)
        self.assertEqual(self.page('follow-up', scope='mine', staff_id=self.staff)['total'], 2)
        newer = fx.legacy_start()
        self.change(newer, self.staff)
        fx.act(newer, 'finish', recommendation='No Further Action', text='Examined.')
        self.assertEqual(self.page('no-action', scope='mine', staff_id=self.staff)['total'], 1)
        self.assertEqual(self.page('no-action', scope='mine', staff_id=self.admin)['total'], 0)
        with self.connect() as connection:
            self.assertEqual(fx.snapshot(connection), fx.originals)

    def test_http_reassignment_csrf_roles_validation_history_and_context(self):
        review_id = self.fixture.start()
        state = urlencode(queue.queue_return_parameters(1, queue.QueueFilters(
            scope='mine', sort='desc', programs=('4Ps',))), doseq=True)
        path = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps/{review_id}?{state}'
        client = self.client()
        form = {'csrf_token': 'test-token', 'batch_id': self.fixture.batch_id,
                'run_id': self.fixture.run_id, 'action': 'reassign', 'responsible_staff_id': self.staff,
                'responsibility_version': 1, 'text': '<script>Coverage</script>'}
        response = client.get(path + '&action=reassign')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Confirm Reassignment', response.text)
        for label in ('Back to Review Queue', 'View Staff Reviews', 'View Household Assessment',
                      'View Subsidy Screening', 'Responsible Staff', 'Started by'):
            self.assertIn(label, response.text)
        self.assertIn('tab=reviews', response.text)
        self.assertIn('#staff-reviews-heading', response.text)
        self.assertEqual(client.post(path + '&action=reassign', data={**form, 'csrf_token': ''}).status_code, 400)
        self.assertEqual(client.post(path + '&action=reassign', data={**form, 'text': ''}).status_code, 400)
        self.assertEqual(client.post(path + '&action=reassign', data={**form, 'responsible_staff_id': 9999}).status_code, 400)
        duplicate = MultiDict(form)
        duplicate.add('responsible_staff_id', self.admin)
        self.assertEqual(client.post(path + '&action=reassign', data=duplicate).status_code, 400)
        response = client.post(path + '&action=reassign', data=form)
        self.assertEqual(response.status_code, 303)
        params = parse_qs(urlsplit(response.location).query)
        self.assertEqual(params['queue_scope'], ['mine'])
        self.assertEqual(params['queue_sort'], ['desc'])
        saved = client.get(response.location)
        self.assertIn('Assignment history', saved.text)
        self.assertIn('&lt;script&gt;Coverage&lt;/script&gt;', saved.text)
        self.assertNotIn('<script>Coverage</script>', saved.text)
        self.assertIn('synthetic_staff (you)', saved.text)
        self.assertEqual(client.post(path + '&action=reassign', data=form).status_code, 409)
        # Administrator also has shared reassignment access.
        admin_client = self.client(self.admin)
        self.assertEqual(admin_client.post(path + '&action=reassign', data={
            **form, 'responsibility_version': 2, 'responsible_staff_id': self.admin,
        }).status_code, 303)
        for status, _ in queue.STATUS_OPTIONS:
            page = client.get(f'/assessments/queue?scope=mine&sort=asc&status={status}')
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('aria-label="Assessment views"', page.text)
            self.assertIn('class="navigation-group" open', page.text)
            self.assertIn('Household Prioritization', page.text)
            status_links = re.search(r'aria-label="Review status".*?</nav>', page.text, re.S)[0]
            self.assertIn('scope=mine', unescape(status_links))
            self.assertIn('sort=asc', unescape(status_links))
        with self.connect() as connection:
            connection.execute('UPDATE staff_users SET is_active=false WHERE id=%s', (self.staff,))
        self.assertEqual(client.post(path + '&action=reassign', data=form).location, '/login')

    def test_forward_migration_backfills_active_closed_cancelled_without_history_loss(self):
        fx = self.fixture
        ids = [fx.start(), fx.start('SLP', 'Examine.'), fx.start('TUPAD')]
        fx.act(ids[0], 'note', text='Original note.')
        fx.act(ids[1], 'finish', program='SLP', recommendation='No Further Action', text='Examined.')
        fx.act(ids[2], 'cancel', program='TUPAD')
        # Recreate the exact pre-0007 shape inside an isolated transaction, then migrate it.
        with self.connect() as connection:
            connection.execute('UPDATE staff_users SET is_active=false WHERE id=%s', (self.admin,))
            connection.execute('DROP TABLE staff_review_reassignments')
            connection.execute('ALTER TABLE staff_reviews DROP COLUMN responsible_staff_id, DROP COLUMN responsibility_version')
            before = connection.execute('SELECT * FROM staff_reviews ORDER BY id').fetchall()
            notes = connection.execute('SELECT * FROM staff_review_notes ORDER BY id').fetchall()
            source = fx.snapshot(connection)
            connection.execute((reviews.ROOT / 'migrations/0007_review_responsibility.sql').read_text(encoding='utf-8'))
            after = connection.execute('SELECT * FROM staff_reviews ORDER BY id').fetchall()
            self.assertEqual([row[:len(before[0])] for row in after], before)
            self.assertEqual([row[-2:] for row in after], [(self.admin, 1)] * 3)
            self.assertEqual(connection.execute('SELECT * FROM staff_review_notes ORDER BY id').fetchall(), notes)
            self.assertEqual(fx.snapshot(connection), source)
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_review_reassignments').fetchone()[0], 0)
        with self.assertRaisesRegex(service.StaffReviewError, 'already_active'):
            fx.start(staff_id=self.staff)

