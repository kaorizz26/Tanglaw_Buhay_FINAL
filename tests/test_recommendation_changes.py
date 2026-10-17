"""Completed changes use existing guarded synthetic schemas and staff sessions."""

from concurrent.futures import ThreadPoolExecutor
from html import unescape
import re
from threading import Event
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import psycopg
from werkzeug.datastructures import MultiDict

from assessment_detail_fixtures import HOUSEHOLD_UUID
from repositories import staff_reviews as repository
from services import recommendation_changes as service, staff_reviews
from services.review_queue import QueueFilters, queue_return_parameters
import test_review_responsibility as responsibility
import test_staff_reviews as reviews


@unittest.skipUnless(reviews.TEST_URL, 'Set the isolated local test database URL.')
class RecommendationChangeTests(unittest.TestCase):
    def setUp(self):
        self.helper = responsibility.ResponsibilityDatabaseTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.fx = self.helper.fixture
        self.connect = self.fx.connect
        self.admin, self.staff = self.fx.staff_ids
        self.review_id = self.fx.start()
        self.fx.act(self.review_id, 'note', text='Original immutable note.')
        self.fx.act(self.review_id, 'reassign', responsible_staff_id=self.staff,
                    responsibility_version=1, text='Original assignment reason.')
        self.fx.act(self.review_id, 'finish', recommendation='For LGU Follow-Up', text='Original explanation.')
        with self.connect() as connection:
            self.original_review = connection.execute('SELECT * FROM staff_reviews ORDER BY id').fetchall()
            self.original_notes = connection.execute('SELECT * FROM staff_review_notes ORDER BY id').fetchall()
            self.original_assignments = connection.execute('SELECT * FROM staff_review_reassignments ORDER BY id').fetchall()

    def change(self, action='correct', **overrides):
        values = {'version': 0, 'reason': 'Correct the saved explanation.',
                  'recommendation': 'No Further Action', 'explanation': 'Updated explanation.',
                  'connection_factory': self.connect, **overrides}
        return service.change_recommendation(
            HOUSEHOLD_UUID, values.pop('program', '4Ps'),
            values.pop('review_id', self.review_id), action,
            values.pop('staff_id', self.staff), values.pop('batch_id', self.fx.batch_id),
            values.pop('run_id', self.fx.run_id), **values,
        )

    def history(self):
        return self.helper.history()

    def test_both_directions_explanation_only_and_attributed_immutable_history(self):
        self.change()
        self.assertEqual(self.helper.page('follow-up')['total'], 0)
        self.assertEqual(self.helper.page('no-action', scope='mine', staff_id=self.staff)['total'], 1)
        self.assertEqual(self.helper.page('no-action', scope='mine', staff_id=self.admin)['total'], 0)
        self.change(version=1, recommendation='For LGU Follow-Up', staff_id=self.admin)
        self.change(version=2, recommendation='For LGU Follow-Up', explanation='Explanation only.')
        row = self.history()[0]
        changes = row['recommendation_changes']
        self.assertEqual(row['effective_recommendation'], 'For LGU Follow-Up')
        self.assertEqual(row['effective_explanation'], 'Explanation only.')
        self.assertEqual(row['recommendation_version'], 3)
        self.assertEqual([c['sequence'] for c in changes], [1, 2, 3])
        self.assertEqual([c['changed_by'] for c in changes], [self.staff, self.admin, self.staff])
        self.assertEqual(changes[0]['previous_explanation'], 'Original explanation.')
        self.assertEqual(changes[1]['previous_recommendation'], 'No Further Action')
        self.assertTrue(all(c['reason'] and c['created_at'] for c in changes))
        self.assertLessEqual(changes[0]['created_at'], changes[-1]['created_at'])
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT * FROM staff_reviews ORDER BY id').fetchall(), self.original_review)
            self.assertEqual(connection.execute('SELECT * FROM staff_review_notes ORDER BY id').fetchall(), self.original_notes)
            self.assertEqual(connection.execute('SELECT * FROM staff_review_reassignments ORDER BY id').fetchall(), self.original_assignments)
            self.assertEqual(self.fx.snapshot(connection), self.fx.originals)
        for statement in ('UPDATE staff_review_recommendation_changes SET reason=reason',
                          'DELETE FROM staff_review_recommendation_changes'):
            with self.assertRaises(psycopg.Error), self.connect() as connection:
                connection.execute(statement)

    def test_validation_and_preview_write_nothing(self):
        cases = (
            ({'reason': ''}, 'invalid_change_reason'),
            ({'reason': '  '}, 'invalid_change_reason'),
            ({'reason': 'x' * 1001}, 'invalid_change_reason'),
            ({'reason': '\x00'}, 'invalid_change_reason'),
            ({'explanation': ''}, 'invalid_change_explanation'),
            ({'explanation': 'x' * 2001}, 'invalid_change_explanation'),
            ({'explanation': '\x00'}, 'invalid_change_explanation'),
            ({'recommendation': ''}, 'invalid_recommendation'),
            ({'recommendation': 'Approved'}, 'invalid_recommendation'),
            ({'recommendation': 'For LGU Follow-Up', 'explanation': ' Original explanation. '}, 'no_change'),
            ({'version': True}, 'invalid_request'),
            ({'version': -1}, 'invalid_request'),
            ({'staff_id': 999999}, 'unauthorized'),
            ({'program': 'SLP'}, 'review_missing'),
            ({'run_id': self.fx.other_run}, 'context_changed'),
        )
        for values, code in cases:
            with self.subTest(values=values), self.assertRaisesRegex(staff_reviews.StaffReviewError, code):
                self.change(**values)
        with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'invalid_change_reason'):
            self.change('withdraw', reason='')
        self.change(save=False)
        self.change('withdraw', save=False)
        self.assertEqual(self.history()[0]['recommendation_changes'], [])

    def test_effective_completion_blocks_direct_and_stale_start_without_losing_context(self):
        with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_exists'):
            self.fx.start()
        client = self.helper.client()
        state = urlencode(queue_return_parameters(2, QueueFilters(
            status='follow-up', scope='mine', sort='asc', search='HH-00008',
            programs=('4Ps',))), doseq=True)
        start = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps?{state}'
        for method in ('GET', 'POST'):
            response = client.open(start, method=method, data={
                'csrf_token': 'test-token', 'batch_id': self.fx.batch_id,
                'run_id': self.fx.run_id})
            self.assertEqual(response.status_code, 303)
            arguments = parse_qs(urlsplit(response.location).query)
            for key, value in parse_qs(state).items():
                self.assertEqual(arguments[key], value)
            page = client.get(response.location)
            card = page.text.split('id="review-4ps"', 1)[1].split('</article>', 1)[0]
            self.assertIn('View Review', card)
            self.assertNotIn('Start Review', card)
            self.assertIn('completed recommendation already exists', page.text)
        self.assertEqual(len(self.history()), 1)
        self.assertIsInstance(self.fx.start('SPISC', 'Independent program.'), int)
        self.change()
        self.assertEqual(self.helper.page('no-action')['total'], 1)
        self.change('withdraw', version=1)
        self.assertEqual(client.get(start).status_code, 200)
        self.assertIsInstance(self.fx.start(), int)

    def test_withdrawn_latest_completion_is_not_skipped_after_later_cancellation(self):
        newer = self.fx.legacy_start()
        self.fx.act(newer, 'finish', recommendation='No Further Action', text='Newer decision.')
        self.change('withdraw', review_id=newer, reason='Withdraw the newer decision.')
        cancelled = self.fx.start()
        self.fx.act(cancelled, 'cancel', text='Accidental start after withdrawal.')
        self.assertEqual(self.helper.page('follow-up')['total'], 0)
        self.assertEqual(self.helper.page('no-action')['total'], 0)
        client = self.helper.client()
        for tab in ('reviews', 'overview'):
            page = client.get(f'/assessments/{HOUSEHOLD_UUID}?tab={tab}')
            self.assertIn('Recommendation Withdrawn', page.text)
        with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_unavailable'):
            self.change()
        history = self.history()
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]['notes'][0]['body'], 'Original immutable note.')
        self.assertTrue(history[1]['withdrawn'])
        self.assertEqual(history[2]['status'], 'Cancelled')
        self.assertIsInstance(self.fx.start(), int)

    def test_finish_serializes_with_stale_start_submission(self):
        self.change('withdraw')
        active = self.fx.start()
        attempted = Event()
        lock = repository.lock_household_reviews

        def signal(*args):
            attempted.set()
            return lock(*args)

        def attempt():
            try:
                self.fx.start()
                return 'started'
            except staff_reviews.StaffReviewError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect() as connection:
                lock(connection, self.fx.batch_id, HOUSEHOLD_UUID)
                with patch.object(repository, 'lock_household_reviews', side_effect=signal):
                    future = pool.submit(attempt)
                    self.assertTrue(attempted.wait(10))
                    repository.finish(connection, active, self.staff, 'For LGU Follow-Up', 'Finished.')
                    connection.commit()
                    self.assertEqual(future.result(timeout=10), 'recommendation_exists')
        self.assertEqual(len(self.history()), 2)

    def test_withdrawal_consistency_no_restoration_other_program_and_restart(self):
        self.change()
        other = self.fx.start('SLP', 'Examine this program.')
        self.fx.act(other, 'finish', program='SLP', recommendation='No Further Action', text='SLP explanation.')
        self.change('withdraw', version=1, reason='Recommendation was recorded in error.')
        row = self.history()[0]
        self.assertTrue(row['withdrawn'])
        self.assertIsNone(row['effective_recommendation'])
        self.assertEqual(row['closing_recommendation'], 'For LGU Follow-Up')
        self.assertEqual(row['recommendation_changes'][-1]['previous_recommendation'], 'No Further Action')
        self.assertEqual(self.helper.page('follow-up')['total'], 0)
        self.assertEqual([r['id'] for r in self.helper.page('no-action')['items']], [other])
        client = self.helper.client()
        base = f'/assessments/{HOUSEHOLD_UUID}'
        for path in (base + '?tab=overview', base + '?tab=reviews', base + f'/reviews/4Ps/{self.review_id}'):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('Recommendation Withdrawn', response.text)
            if '/reviews/4Ps/' in path:
                self.assertNotIn('>Correct Recommendation</a>', response.text)
                self.assertNotIn('>Withdraw Recommendation</a>', response.text)
                for label in ('Original explanation.', 'Recommendation withdrawn', 'synthetic_staff',
                              'Original immutable note.', 'Original assignment reason.'):
                    self.assertIn(label, response.text)
        self.assertIn('/reviews/4Ps?', client.get(base + '?tab=reviews').text)
        for action in ('correct', 'withdraw'):
            with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_unavailable'):
                self.change(action, version=2)
        self.assertIsInstance(self.fx.start(), int)
        with self.connect() as connection:
            self.assertEqual(self.fx.snapshot(connection), self.fx.originals)

    def test_superseded_cancelled_and_active_records_are_read_only(self):
        # Preserve unfinished repeats created before the initiation restriction.
        newer = self.fx.legacy_start()
        for action in ('correct', 'withdraw'):
            with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_unavailable'):
                self.change(action)
            with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_unavailable'):
                self.change(action, review_id=newer)
        self.fx.act(newer, 'cancel')
        self.assertEqual(self.helper.page('follow-up')['total'], 1)
        path = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps/{self.review_id}'
        client = self.helper.client()
        self.assertIn('>Correct Recommendation</a>', client.get(path).text)
        self.assertEqual(client.get(path + '?action=correct').status_code, 200)
        self.change()
        newest = self.fx.legacy_start()
        self.fx.act(newest, 'finish', recommendation='No Further Action', text='New completion.')
        with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'recommendation_unavailable'):
            self.change(version=1)

    def test_concurrent_corrections_and_correction_withdrawal_allow_one_change(self):
        for actions in (('correct', 'correct'), ('correct', 'withdraw')):
            version = self.history()[0]['recommendation_version']
            with ThreadPoolExecutor(max_workers=2) as pool:
                def attempt(action):
                    try:
                        self.change(action, version=version, explanation=f'Updated at {version}')
                        return 'saved'
                    except staff_reviews.StaffReviewError as error:
                        return str(error)
                outcomes = list(pool.map(attempt, actions))
            self.assertEqual(outcomes.count('saved'), 1)
            self.assertTrue(set(outcomes) <= {'saved', 'recommendation_changed', 'recommendation_unavailable'})
        self.assertEqual(len(self.history()[0]['recommendation_changes']), 2)

    def test_new_start_wins_lock_and_correction_rolls_back_on_failure(self):
        attempted = Event()
        original = repository.lock_household_reviews

        def signal(*args):
            attempted.set()
            return original(*args)

        def attempt():
            try:
                self.change()
            except staff_reviews.StaffReviewError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connect() as connection:
                original(connection, self.fx.batch_id, HOUSEHOLD_UUID)
                with patch.object(repository, 'lock_household_reviews', side_effect=signal):
                    future = pool.submit(attempt)
                    self.assertTrue(attempted.wait(10))
                    repository.insert_active(connection, batch_id=self.fx.batch_id,
                                             household_uuid=HOUSEHOLD_UUID, run_id=self.fx.run_id,
                                             program='4Ps', starting_reason=None, staff_id=self.staff)
                    connection.commit()
                    self.assertEqual(future.result(timeout=10), 'recommendation_unavailable')
        other = self.fx.start('SLP', 'Examine.')
        self.fx.act(other, 'finish', program='SLP', recommendation='For LGU Follow-Up', text='SLP.')
        append = repository.append_recommendation_change

        def failing(*args):
            append(*args)
            raise RuntimeError('Synthetic failure')
        with patch.object(repository, 'append_recommendation_change', side_effect=failing):
            with self.assertRaisesRegex(staff_reviews.StaffReviewError, 'unavailable'):
                self.change(review_id=other, program='SLP')
        self.assertEqual(self.history()[-1]['recommendation_changes'], [])

    def token(self, response):
        self.assertEqual(response.status_code, 200)
        return unescape(re.search(r'name="confirmation_token" value="([^"]+)"', response.text)[1])

    def form(self, **values):
        return {'csrf_token': 'test-token', 'step': 'review', 'batch_id': self.fx.batch_id,
                'run_id': self.fx.run_id, 'version': 0, 'reason': '<script>Correction reason</script>',
                'recommendation': 'No Further Action', 'explanation': 'Corrected explanation.', **values}

    def test_http_two_step_shared_access_confirmation_tampering_csrf_and_safe_returns(self):
        client = self.helper.client()
        state = urlencode(queue_return_parameters(2, QueueFilters(
            'follow-up', 'HH-00008', 'Synthetic Barangay', ('4Ps', 'SLP'), 'mine', 'asc')), doseq=True)
        base = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps/{self.review_id}?{state}'
        path = base + '&action=correct'
        page = client.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('checked', page.text)
        for label in ('Previously saved recommendation', 'Updated explanation (required)', 'Cancel'):
            self.assertIn(label, page.text)
        self.assertEqual(client.post(path, data=self.form(csrf_token='')).status_code, 400)
        self.assertEqual(client.post(path, data=self.form(reason='')).status_code, 400)
        duplicated = MultiDict(self.form())
        duplicated.add('reason', 'Other reason')
        self.assertEqual(client.post(path, data=duplicated).status_code, 400)
        response = client.post(path, data=self.form())
        token = self.token(response)
        self.assertIn('&lt;script&gt;Correction reason&lt;/script&gt;', response.text)
        self.assertEqual(self.history()[0]['recommendation_changes'], [])
        edited = client.post(path, data=self.form(step='edit', confirmation_token=token))
        self.assertIn('Corrected explanation.', edited.text)
        self.assertNotIn('Confirm and Save', edited.text)
        self.assertEqual(self.history()[0]['recommendation_changes'], [])
        self.assertEqual(client.post(path, data=self.form(step='confirm', confirmation_token=token + 'x')).status_code, 400)
        # Confirmation is bound to the staff member and action, not mutable POST fields.
        self.assertEqual(self.helper.client(self.admin).post(path, data=self.form(
            step='confirm', confirmation_token=token)).status_code, 400)
        saved = client.post(path, data=self.form(step='confirm', confirmation_token=token,
                                                recommendation='For LGU Follow-Up', explanation='Tampered'))
        self.assertEqual(saved.status_code, 303)
        params = parse_qs(urlsplit(saved.location).query)
        for key, value in parse_qs(state).items():
            self.assertEqual(params[key], value)
        self.assertEqual(self.history()[0]['effective_recommendation'], 'No Further Action')
        self.assertEqual(self.history()[0]['effective_explanation'], 'Corrected explanation.')
        for tab in ('overview', 'reviews'):
            response = client.get(f'/assessments/{HOUSEHOLD_UUID}?tab={tab}')
            self.assertIn('No Further Action', response.text)
        self.assertEqual(client.post(path, data=self.form(step='confirm', confirmation_token=token)).status_code, 409)
        self.assertEqual(client.get(base + '&action=correct&queue_url=https://example.com').status_code, 400)
        # Administrator can withdraw a recommendation completed/corrected by another staff member.
        admin = self.helper.client(self.admin)
        withdrawal = base + '&action=withdraw'
        preview = admin.post(withdrawal, data=self.form(version=1))
        self.assertIn('Review Withdrawal', preview.text)
        self.assertIn('HH-00008', preview.text)
        self.assertIn('Keep Current Recommendation', preview.text)
        withdrawn = admin.post(withdrawal, data=self.form(step='confirm', confirmation_token=self.token(preview)))
        self.assertEqual(withdrawn.status_code, 303)
        self.assertEqual(self.history()[0]['recommendation_changes'][-1]['changed_by'], self.admin)

    def test_http_authentication_and_stale_confirmation_after_newer_start(self):
        path = f'/assessments/{HOUSEHOLD_UUID}/reviews/4Ps/{self.review_id}?action=correct'
        client = self.helper.client()
        token = self.token(client.post(path, data=self.form()))
        self.fx.legacy_start()
        self.assertEqual(client.post(path, data=self.form(step='confirm', confirmation_token=token)).status_code, 409)
        self.assertEqual(self.history()[0]['recommendation_changes'], [])
        with self.connect() as connection:
            connection.execute('UPDATE staff_users SET is_active=false WHERE id=%s', (self.staff,))
        self.assertEqual(client.post(path, data=self.form()).location, '/login')
        with self.connect() as connection:
            connection.execute('ALTER TABLE staff_users DROP CONSTRAINT staff_users_role_check')
            connection.execute("UPDATE staff_users SET is_active=true, role='observer' WHERE id=%s", (self.staff,))
        self.assertEqual(client.get(path).location, '/login')

    def test_forward_migration_preserves_existing_records(self):
        with self.connect() as connection:
            connection.execute('DROP VIEW staff_review_recommendation_state')
            connection.execute('DROP TABLE staff_review_recommendation_changes')
            connection.execute('DROP FUNCTION prevent_recommendation_history_change()')
            connection.execute((reviews.ROOT / 'migrations/0008_recommendation_changes.sql').read_text(encoding='utf-8'))
            self.assertEqual(connection.execute('SELECT * FROM staff_reviews ORDER BY id').fetchall(), self.original_review)
            self.assertEqual(connection.execute('SELECT * FROM staff_review_notes ORDER BY id').fetchall(), self.original_notes)
            self.assertEqual(repository.recommendation_state(connection, self.review_id)['recommendation_version'], 0)
            self.assertEqual(self.fx.snapshot(connection), self.fx.originals)
