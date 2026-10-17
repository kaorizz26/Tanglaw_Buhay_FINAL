"""Synthetic initiation tests; writes are confined to disposable local test schemas."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
from secrets import token_urlsafe
from threading import Barrier
from time import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from repositories import assessment_runs, staff_reviews as repository
from services import staff_reviews as service
from services.assessment_details import prepare_assessment_detail
from services.assessment_queries import AssessmentQueryError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
TEST_URL = os.getenv('TANGLAW_TEST_DATABASE_URL')


def review_source():
    source = detail_source()
    source['staff_reviews'] = []
    return source


class StaffReviewRouteTests(unittest.TestCase):
    def setUp(self):
        self.source = review_source()
        self.app = create_app({
            'TESTING': True, 'SECRET_KEY': token_urlsafe(48), 'DATABASE_URL': None,
        })
        self.client = self.app.test_client()
        self.user = {'id': 7, 'username': 'synthetic_staff', 'role': 'lgu_staff',
                     'is_active': True, 'session_version': 1}
        patches = (
            patch('psycopg.connect', side_effect=AssertionError('Real connection blocked')),
            patch('services.auth.get_connection', return_value=MagicMock()),
            patch('repositories.staff_users.find_by_id', side_effect=lambda *args: dict(self.user)),
            patch.object(service, 'get_active_assessment_detail', return_value=self.source),
            patch('routes.assessments.get_assessment_detail',
                  side_effect=lambda *args: prepare_assessment_detail(self.source)),
            patch('routes.assessments.start_staff_review', return_value=42),
        )
        for patcher in patches:
            self.creator = patcher.start()
            self.addCleanup(patcher.stop)
        self.path = f'/assessments/{HOUSEHOLD_UUID}'
        self.start_path = self.path + '/reviews/4Ps'

    def session(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, session_version=1, issued_at=int(time()), csrf_token='test-token')

    def post(self, program='4Ps', **overrides):
        data = {'csrf_token': 'test-token', 'batch_id': '1', 'run_id': '2', **overrides}
        return self.client.post(self.path + '/reviews/' + program, data=data)

    def test_both_roles_start_only_by_post_and_return_to_reviews(self):
        before = deepcopy(self.source)
        for role in ('system_administrator', 'lgu_staff'):
            with self.subTest(role=role):
                self.user['role'] = role
                self.session()
                page = self.client.get(self.start_path + '?page=2&priority=both')
                self.assertEqual(page.status_code, 200)
                self.assertIn('Start 4Ps Review', page.text)
                self.assertIn('HH-00008', page.text)
                self.assertNotIn('<textarea', page.text)
                self.creator.assert_not_called()
                result = self.client.post(
                    self.start_path + '?page=2&priority=both',
                    data={'csrf_token': 'test-token', 'batch_id': '1', 'run_id': '2',
                          'staff_id': '999'},
                )
                self.assertEqual(result.status_code, 303)
                self.assertIn('tab=reviews', result.location)
                self.assertIn('page=2', result.location)
                self.assertIn('priority=both', result.location)
                self.creator.assert_called_once_with(HOUSEHOLD_UUID, '4Ps', '', 7, 1, 2)
                self.creator.reset_mock()
        self.assertEqual(self.source, before)

    def test_unauthenticated_inactive_unknown_role_and_csrf_cannot_create(self):
        self.assertEqual(self.post().location, '/login')
        for change in ({'is_active': False}, {'role': 'citizen'}, {'session_version': 8}):
            original = dict(self.user)
            self.user.update(change)
            self.session()
            self.assertEqual(self.post().location, '/login')
            self.user.clear()
            self.user.update(original)
        self.session()
        for token in ('', 'wrong'):
            self.assertEqual(self.post(csrf_token=token).status_code, 400)
        self.creator.assert_not_called()

    def test_reason_form_only_when_required_and_validation_is_accessible(self):
        self.session()
        for program, label in (('SLP', 'Not Enough Information'), ('SPISC', 'No Review Signal')):
            page = self.client.get(self.path + '/reviews/' + program)
            self.assertEqual(page.status_code, 200)
            self.assertIn(label, page.text)
            self.assertIn('name="starting_reason"', page.text)
            self.assertIn('maxlength="1000" required', page.text)
            self.creator.side_effect = service.StaffReviewError('reason_required')
            response = self.post(program)
            self.assertEqual(response.status_code, 400)
            self.assertIn('role="alert"', response.text)
            self.assertIn('aria-invalid="true"', response.text)
        self.creator.side_effect = None
        self.assertEqual(self.post('SLP', starting_reason='Staff received a request.').status_code, 303)

    def test_reviews_display_account_date_reason_and_no_duplicate_actions(self):
        self.session()
        self.source['staff_reviews'] = [{
            'id': 42, 'status': 'In Review',
            'program': 'SLP', 'username': 'synthetic_admin', 'starting_reason': '<script>reason</script>',
            'created_at': datetime(2026, 10, 17, tzinfo=timezone.utc),
        }]
        response = self.client.get(self.path + '?tab=reviews')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('class="staff-review-card"'), 4)
        for text in ('Not Started', 'In Review', 'synthetic_admin', '17 Oct 2026',
                     'Not Enough Information', 'No Review Signal', '&lt;script&gt;reason&lt;/script&gt;'):
            self.assertIn(text, response.text)
        self.assertNotIn('program=SLP', response.text)
        self.assertIn('/reviews/SLP/42', response.text)
        self.assertIn('Open Review', response.text)
        self.assertIn('Automated historical screening finding', response.text)
        self.assertIn('Staff review status', response.text)
        self.assertIn('Staff recommendations are recorded separately', response.text)
        for text in ('Continue Review', 'Close Review', 'Add Notes'):
            self.assertNotIn(text, response.text)
        self.assertEqual(self.client.get(self.path + '/reviews/SLP').status_code, 303)
        self.creator.assert_not_called()
        for tab in ('overview', 'mpi', 'screening'):
            self.assertEqual(self.client.get(self.path + '?tab=' + tab).status_code, 200)

    def test_no_review_status_panel_is_only_in_overview_with_direct_navigation(self):
        self.session()
        for tab in ('overview', 'mpi', 'screening', 'reviews'):
            with self.subTest(tab=tab):
                response = self.client.get(self.path + '?tab=' + tab + '&page=2&priority=both')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text.count('class="assessment-metrics"'), 1)
                if tab != 'overview':
                    self.assertNotIn('id="review-status-heading"', response.text)
                    self.assertNotIn('No Staff Review Started', response.text)
                    if tab == 'reviews':
                        self.assertEqual(response.text.count('class="staff-review-card"'), 4)
                    continue
                self.assertIn('No Staff Review Started', response.text)
                self.assertIn(
                    'LGU staff have not yet started a subsidy review for this household.',
                    response.text,
                )
                self.assertIn('View Staff Reviews →', response.text)
                self.assertNotIn('class="staff-review-card"', response.text)
                self.assertIn(
                    'tab=reviews&amp;page=2&amp;priority=both#staff-reviews-heading',
                    response.text,
                )
                positions = [response.text.index(marker) for marker in (
                    'class="overview-left"', 'id="review-status-heading"',
                    'id="overview-heading"', 'id="screening-heading"',
                )]
                self.assertEqual(positions, sorted(positions))
                for destination, label in (
                    ('mpi', 'View MPI Indicators →'),
                    ('screening', 'View Subsidy Screening →'),
                ):
                    self.assertIn(label, response.text)
                    self.assertIn(
                        f'tab={destination}&amp;page=2&amp;priority=both', response.text,
                    )
                self.assertEqual(response.text.count('class="review-button'), 3)

    def test_latest_review_summary_and_program_cards_keep_outcomes_separate(self):
        self.session()
        first = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.source['staff_reviews'] = [
            {
                'id': 41, 'program': '4Ps', 'status': 'In Review',
                'username': 'synthetic_staff', 'starting_reason': 'Staff examination requested.',
                'created_at': first, 'closing_recommendation': None,
            },
            {
                'id': 42, 'program': 'SLP', 'status': 'Closed',
                'username': 'synthetic_staff', 'starting_reason': 'Review initiated.',
                'created_at': first, 'closing_recommendation': 'For LGU Follow-Up',
                'closing_explanation': 'Recommend further consideration.',
            },
            {
                'id': 43, 'program': 'SPISC', 'status': 'Closed',
                'username': 'synthetic_staff', 'starting_reason': 'Review initiated.',
                'created_at': first, 'closing_recommendation': 'No Further Action',
                'closing_explanation': 'No further action is recommended.',
            },
            {
                'id': 44, 'program': 'TUPAD', 'status': 'Closed',
                'username': 'synthetic_staff', 'starting_reason': 'Review initiated.',
                'created_at': first, 'closing_recommendation': 'For LGU Follow-Up',
                'closing_explanation': 'Earlier recommendation.',
            },
            {
                'id': 45, 'program': 'TUPAD', 'status': 'Cancelled',
                'username': 'synthetic_staff', 'starting_reason': 'Review initiated.',
                'created_at': first.replace(day=2), 'closing_recommendation': None,
                'cancellation_reason': 'The review was started in error.',
            },
        ]

        for tab in ('overview', 'mpi', 'screening'):
            with self.subTest(tab=tab):
                response = self.client.get(self.path + '?tab=' + tab)
                self.assertEqual(response.status_code, 200)
                if tab != 'overview':
                    self.assertNotIn('id="review-status-heading"', response.text)
                    continue
                for text in ('In Review', 'For LGU Follow-Up', 'No Further Action', 'Cancelled'):
                    self.assertIn(text, response.text)
                status_list = response.text.split('<ul class="review-status-list">', 1)[1].split(
                    '</ul>', 1,
                )[0]
                tupad_summary = status_list.split('<strong>TUPAD</strong>', 1)[1].split(
                    '</li>', 1,
                )[0]
                self.assertIn('Cancelled', tupad_summary)
                self.assertNotIn('For LGU Follow-Up', tupad_summary)

        reviews = self.client.get(self.path + '?tab=reviews')
        self.assertEqual(reviews.status_code, 200)
        self.assertNotIn('id="review-status-heading"', reviews.text)
        self.assertIn('id="staff-reviews-heading" tabindex="-1"', reviews.text)
        self.assertEqual(reviews.text.count('class="staff-review-card"'), 4)
        for text in ('Pantawid Pamilyang Pilipino Program',
                     'Sustainable Livelihood Program',
                     'Social Pension for Indigent Senior Citizens',
                     'Tulong Panghanapbuhay sa Ating Disadvantaged / Displaced Workers',
                     'Staff recommendation', 'For LGU Follow-Up', 'No Further Action',
                     'Cancelled', 'View Review History (2)'):
            self.assertIn(text, reviews.text)
        self.assertNotIn('Closed', reviews.text)
        tupad_card = reviews.text.split('id="review-tupad"', 1)[1].split(
            '</article>', 1,
        )[0]
        tupad_current = tupad_card.split('<details class="review-history">', 1)[0]
        self.assertIn('Cancelled', tupad_current)
        self.assertNotIn('For LGU Follow-Up', tupad_current)
        self.assertIn('Start Review', tupad_card)
        self.assertIn('For LGU Follow-Up', tupad_card)

        self.source['staff_reviews'] = [{
            'id': 46, 'program': 'SLP', 'status': 'Cancelled',
            'username': 'synthetic_staff', 'starting_reason': 'Review initiated.',
            'created_at': first, 'closing_recommendation': None,
            'cancellation_reason': 'The review was started in error.',
        }]
        cancelled_only = self.client.get(self.path + '?tab=reviews')
        self.assertEqual(cancelled_only.status_code, 200)
        self.assertIn('View Review History (1)', cancelled_only.text)
        self.assertNotIn('Closed', cancelled_only.text)
        slp_card = cancelled_only.text.split('id="review-slp"', 1)[1].split(
            '</article>', 1,
        )[0]
        slp_current = slp_card.split('<details class="review-history">', 1)[0]
        self.assertIn('Cancelled', slp_current)
        self.assertNotIn('Staff recommendation', slp_current)
        self.assertIn('This review ended without a recommendation.', slp_current)
        self.assertIn('Start Review', slp_current)

    def test_invalid_program_context_duplicate_and_database_errors_are_safe(self):
        self.session()
        self.assertEqual(self.post('OTHER').status_code, 404)
        self.assertEqual(self.post(batch_id='invalid').status_code, 400)
        duplicate_values = MultiDict([
            ('csrf_token', 'test-token'), ('batch_id', '1'), ('batch_id', '2'), ('run_id', '2'),
        ])
        self.assertEqual(self.client.post(self.start_path, data=duplicate_values).status_code, 400)
        self.creator.assert_not_called()
        for code, expected in (('already_active', 303), ('context_changed', 400),
                               ('household_missing', 404), ('unauthorized', 403),
                               ('unavailable', 503)):
            self.creator.side_effect = service.StaffReviewError(code)
            response = self.post()
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('Traceback', response.text)
        with patch.object(service, 'get_active_assessment_detail',
                          side_effect=AssessmentQueryError('assessment_query_unavailable')):
            self.assertEqual(self.client.get(self.path + '?tab=reviews').status_code, 503)


@unittest.skipUnless(TEST_URL, 'Set the isolated local test database URL.')
class StaffReviewDatabaseTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get('dbname'), 'tanglaw_buhay_test')
        self.assertIn(settings.get('host'), ('localhost', '127.0.0.1', '::1'))
        self.assertIn(settings.get('hostaddr', '127.0.0.1'), ('127.0.0.1', '::1'))
        self.assertNotIn('service', settings)
        self.assertNotIn('options', settings)
        self.assertFalse(any(os.getenv(name) for name in ('PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')))
        self.schema = 'review_check_' + uuid4().hex
        self.address = '::1' if settings['host'] == '::1' else '127.0.0.1'
        self.url = make_conninfo(TEST_URL, hostaddr=self.address, connect_timeout=5,
                                 options=f'-c search_path={self.schema}')
        self.source = review_source()
        with psycopg.connect(TEST_URL, hostaddr=self.address) as connection:
            self.assertEqual(connection.info.dbname, 'tanglaw_buhay_test')
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
            connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(self.schema)))
            for path in sorted((ROOT / 'migrations').glob('*.sql')):
                connection.execute(path.read_text(encoding='utf-8'))
        self.addCleanup(self.drop_schema)
        with self.connect() as connection:
            self.staff_ids = []
            password_hash = generate_password_hash(token_urlsafe(24))
            for username, role in (('synthetic_admin', 'system_administrator'),
                                   ('synthetic_staff', 'lgu_staff')):
                self.staff_ids.append(connection.execute(
                    'INSERT INTO staff_users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id',
                    (username, password_hash, role),
                ).fetchone()[0])
            self.batch_id, self.run_id = self.make_batch(connection, active=True)
            self.other_batch, self.other_run = self.make_batch(connection, active=False)
            self.originals = self.snapshot(connection)

    def connect(self):
        return psycopg.connect(self.url)

    def drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr=self.address) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def make_batch(self, connection, *, active):
        batch_id = connection.execute(
            """INSERT INTO dataset_batches
               (survey_year, municipality, source_columns, validation_status, is_active)
               VALUES (2022, 'Pozorrubio', '{}'::jsonb, 'validated', %s) RETURNING id""", (active,),
        ).fetchone()[0]
        connection.execute('INSERT INTO households VALUES (%s, %s, 8, %s)',
                           (batch_id, HOUSEHOLD_UUID, Jsonb(self.source['household'])))
        run_id = assessment_runs.create_run(
            connection, batch_id, calculation_version='synthetic',
            calculation_provenance={'kind': 'synthetic stored test findings'},
        )
        assessment_runs.store_household_assessments(
            connection, run_id, {HOUSEHOLD_UUID: self.source['findings']},
        )
        assessment_runs.complete_run(connection, run_id)
        assessment_runs.designate_run(connection, batch_id, run_id)
        return batch_id, run_id

    def snapshot(self, connection):
        return {table: connection.execute(sql.SQL('SELECT * FROM {} ORDER BY 1, 2').format(
            sql.Identifier(table),
        )).fetchall() for table in (
            'dataset_batches', 'households', 'household_members', 'member_health',
            'child_death_records', 'codebook_values', 'mpi_code_mappings',
            'variable_definitions', 'assessment_runs', 'household_assessments',
            'assessment_run_selections',
        )}

    def start(self, program='4Ps', reason='', staff_id=None, **overrides):
        return service.start_staff_review(
            overrides.get('household_uuid', HOUSEHOLD_UUID), program, reason,
            staff_id or self.staff_ids[0], overrides.get('batch_id', self.batch_id),
            overrides.get('run_id', self.run_id), connection_factory=self.connect,
        )

    def test_both_roles_all_programs_persist_exact_identities_without_source_changes(self):
        for index, program in enumerate(('4Ps', 'SLP', 'SPISC', 'TUPAD')):
            staff_id = self.staff_ids[index % 2]
            self.start(program, '  Staff request for consideration.  ', staff_id)
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT batch_id, household_uuid, assessment_run_id, program, status, starting_reason, started_by, created_at FROM staff_reviews ORDER BY id',
            ).fetchall()
            self.assertEqual(len(rows), 4)
            for index, row in enumerate(rows):
                self.assertEqual(row[:3], (self.batch_id, HOUSEHOLD_UUID, self.run_id))
                self.assertEqual(row[4:7], ('In Review', 'Staff request for consideration.', self.staff_ids[index % 2]))
                self.assertIsNotNone(row[7])
            self.assertEqual(self.snapshot(connection), self.originals)
            self.assertEqual(len(repository.list_active(connection, self.batch_id, HOUSEHOLD_UUID)), 4)
            self.assertEqual(repository.list_active(connection, self.other_batch, HOUSEHOLD_UUID), [])

    def test_stored_status_requires_reason_for_both_non_signal_states(self):
        for program in ('SLP', 'SPISC'):
            for reason in ('', ' \n '):
                with self.assertRaisesRegex(service.StaffReviewError, 'reason_required'):
                    self.start(program, reason)
            self.start(program, 'Staff identified a circumstance to review.')
        self.start('4Ps')
        self.start('TUPAD')

    def test_invalid_inactive_stale_and_unassessed_requests_save_nothing(self):
        for program, reason, overrides, code in (
            ('INVALID', '', {}, 'invalid_program'),
            ('4Ps', 'x' * 1001, {}, 'invalid_reason'),
            ('4Ps', '\x00', {}, 'invalid_reason'),
            ('4Ps', '', {'household_uuid': str(uuid4())}, 'household_missing'),
            ('4Ps', '', {'batch_id': self.other_batch, 'run_id': self.other_run}, 'context_changed'),
            ('4Ps', '', {'run_id': self.other_run}, 'context_changed'),
            ('4Ps', '', {'staff_id': 99999}, 'unauthorized'),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(service.StaffReviewError, code):
                self.start(program, reason, **overrides)
        with self.connect() as connection:
            connection.execute('UPDATE staff_users SET is_active = false WHERE id = %s', (self.staff_ids[0],))
        with self.assertRaisesRegex(service.StaffReviewError, 'unauthorized'):
            self.start()
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 0)

    def test_concurrent_and_repeated_initiations_create_exactly_one_review(self):
        ready = Barrier(2)
        insert = repository.insert_active

        def simultaneous_insert(connection, **kwargs):
            ready.wait(timeout=10)
            return insert(connection, **kwargs)

        def attempt(_):
            try:
                return self.start()
            except service.StaffReviewError as error:
                return str(error)
        with patch.object(repository, 'insert_active', side_effect=simultaneous_insert), \
                ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, range(2)))
        self.assertEqual(outcomes.count('already_active'), 1)
        self.assertEqual(sum(type(value) is int for value in outcomes), 1)
        with self.assertRaisesRegex(service.StaffReviewError, 'already_active'):
            self.start()
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 1)

    def test_database_failure_rolls_back_insert_and_hides_details(self):
        original = repository.insert_active

        def failing(connection, **kwargs):
            original(connection, **kwargs)
            raise psycopg.OperationalError('PRIVATE_CONNECTION_DETAILS')

        with patch.object(repository, 'insert_active', side_effect=failing):
            with self.assertRaisesRegex(service.StaffReviewError, '^unavailable$'):
                self.start()
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 0)

    def test_authenticated_http_round_trip_uses_real_storage_and_read_only_detail(self):
        app = create_app({
            'TESTING': True, 'SECRET_KEY': token_urlsafe(48), 'DATABASE_URL': self.url,
        })
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=self.staff_ids[1], session_version=1,
                           issued_at=int(time()), csrf_token='test-token')
        path = f'/assessments/{HOUSEHOLD_UUID}'
        self.assertEqual(client.get(path + '?tab=reviews').status_code, 200)
        self.assertEqual(client.get(path + '/reviews/SLP').status_code, 200)
        response = client.post(path + '/reviews/SLP', data={
            'csrf_token': 'test-token', 'batch_id': self.batch_id, 'run_id': self.run_id,
            'starting_reason': 'Staff received a request for consideration.',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        for text in ('In Review', 'synthetic_staff', 'Staff received a request for consideration.',
                     'The SLP staff review has started.'):
            self.assertIn(text, response.text)
        self.assertIn('Open Review', response.text)
        with self.connect() as connection:
            self.assertEqual(self.snapshot(connection), self.originals)

    def test_run_redesignation_rejects_stale_form_and_preserves_existing_review(self):
        self.start()
        with self.connect() as connection:
            new_run = assessment_runs.create_run(
                connection, self.batch_id, calculation_version='synthetic-second',
                calculation_provenance={'kind': 'synthetic stored test findings'},
            )
            assessment_runs.store_household_assessments(
                connection, new_run, {HOUSEHOLD_UUID: self.source['findings']},
            )
            assessment_runs.complete_run(connection, new_run)
            assessment_runs.designate_run(connection, self.batch_id, new_run)
        with self.assertRaisesRegex(service.StaffReviewError, 'context_changed'):
            self.start('TUPAD')
        self.start('TUPAD', run_id=new_run)
        with self.connect() as connection:
            self.assertEqual(len(repository.list_active(connection, self.batch_id, HOUSEHOLD_UUID)), 2)
            self.assertEqual(connection.execute(
                "SELECT assessment_run_id FROM staff_reviews WHERE program = '4Ps'",
            ).fetchone()[0], self.run_id)

    def test_database_references_and_unique_index_reject_invalid_direct_writes(self):
        self.start()
        with self.connect() as connection:
            for changes, exception in (
                ({'staff_id': 99999, 'program': 'TUPAD'}, psycopg.errors.ForeignKeyViolation),
                ({'batch_id': self.other_batch, 'program': 'TUPAD'}, psycopg.errors.ForeignKeyViolation),
                ({'program': 'INVALID'}, psycopg.errors.CheckViolation),
            ):
                values = {'batch_id': self.batch_id, 'household_uuid': HOUSEHOLD_UUID,
                          'run_id': self.run_id, 'program': '4Ps', 'starting_reason': None,
                          'staff_id': self.staff_ids[0], **changes}
                with self.assertRaises(exception), connection.transaction():
                    repository.insert_active(connection, **values)
            with self.assertRaises(psycopg.errors.UniqueViolation), connection.transaction():
                connection.execute(
                    """INSERT INTO staff_reviews
                       (batch_id, household_uuid, assessment_run_id, program, started_by,
                        responsible_staff_id)
                       VALUES (%s, %s, %s, '4Ps', %s, %s)""",
                    (self.batch_id, HOUSEHOLD_UUID, self.run_id, *([self.staff_ids[0]] * 2)),
                )

    def act(self, review_id, action, *, staff_id=None, **values):
        return service.act_on_review(
            HOUSEHOLD_UUID, values.pop('program', '4Ps'), review_id, action,
            staff_id or self.staff_ids[1], values.pop('batch_id', self.batch_id),
            values.pop('run_id', self.run_id), connection_factory=self.connect, **values,
        )

    def test_notes_outcomes_history_and_restarting_preserve_all_findings(self):
        first = self.start()
        self.act(first, 'note', text='  Examined information supplied by the household.  ')
        self.act(first, 'note', staff_id=self.staff_ids[0], text='Follow-up observation.')
        self.act(first, 'finish', recommendation='For LGU Follow-Up', text='Recommend office follow-up.')
        for action in ('note', 'finish', 'cancel'):
            with self.assertRaisesRegex(service.StaffReviewError, 'not_active'):
                self.act(first, action, text='Repeated action.', recommendation='No Further Action')
        second = self.start()
        self.act(second, 'cancel', text='Started by mistake.')
        with self.assertRaisesRegex(service.StaffReviewError, 'not_active'):
            self.act(second, 'note', text='Cannot add this.')
        third = self.start()
        self.act(third, 'finish', recommendation='No Further Action', text='No further action at this time.')
        fourth = self.start()
        with self.connect() as connection:
            history = repository.list_history(connection, self.batch_id, HOUSEHOLD_UUID)
            self.assertEqual([row['id'] for row in history], [first, second, third, fourth])
            self.assertEqual([row['status'] for row in history], ['Closed', 'Cancelled', 'Closed', 'In Review'])
            self.assertEqual([note['authored_by'] for note in history[0]['notes']], self.staff_ids[::-1])
            self.assertEqual(history[0]['finished_by'], self.staff_ids[1])
            self.assertIsNotNone(history[0]['finished_at'])
            self.assertEqual(history[1]['cancelled_by'], self.staff_ids[1])
            self.assertIsNotNone(history[1]['cancelled_at'])
            self.assertIsNone(history[1]['closing_recommendation'])
            self.assertEqual(len(repository.list_active(connection, self.batch_id, HOUSEHOLD_UUID)), 1)
            self.assertEqual(self.snapshot(connection), self.originals)

    def test_invalid_actions_associations_and_required_fields_write_nothing(self):
        review_id = self.start()
        cases = [
            ('note', {'text': ' \n '}, 'text_required'),
            ('note', {'text': 'x' * 2001}, 'invalid_text'),
            ('cancel', {'text': 'x' * 1001}, 'invalid_text'),
            ('finish', {'text': 'Reason', 'recommendation': 'Approved'}, 'invalid_recommendation'),
            ('finish', {'recommendation': 'No Further Action'}, 'text_required'),
            ('note', {'text': 'Reason', 'program': 'TUPAD'}, 'review_missing'),
            ('note', {'text': 'Reason', 'batch_id': self.other_batch,
                      'run_id': self.other_run}, 'context_changed'),
            ('note', {'text': 'Reason', 'staff_id': 999999}, 'unauthorized'),
            ('delete', {}, 'invalid_request'),
        ]
        for action, values, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(service.StaffReviewError, error):
                self.act(review_id, action, **values)
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_review_notes').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT status FROM staff_reviews').fetchone()[0], 'In Review')
            self.assertEqual(self.snapshot(connection), self.originals)

    def test_competing_finish_and_cancel_have_one_winner(self):
        review_id = self.start()
        barrier = Barrier(2)

        def perform(action):
            barrier.wait(timeout=10)
            try:
                self.act(review_id, action, text='Completed examination.',
                         recommendation='No Further Action')
                return 'saved'
            except service.StaffReviewError as error:
                return str(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(perform, ('finish', 'cancel')))
        self.assertCountEqual(results, ['saved', 'not_active'])
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_reviews').fetchone()[0], 1)
            self.assertEqual(self.snapshot(connection), self.originals)

    def test_migration_completion_constraints_and_action_rollback(self):
        review_id = self.start()
        with self.connect() as connection:
            for statement in (
                "UPDATE staff_reviews SET status = 'Closed'",
                "UPDATE staff_reviews SET status = 'Cancelled', closing_recommendation = 'No Further Action'",
            ):
                with self.assertRaises(psycopg.errors.CheckViolation), connection.transaction():
                    connection.execute(statement)
        original = repository.add_note

        def fail_after_note(*args):
            original(*args)
            raise RuntimeError('PRIVATE DATABASE DETAIL')

        with patch.object(repository, 'add_note', side_effect=fail_after_note):
            with self.assertRaisesRegex(service.StaffReviewError, '^unavailable$'):
                self.act(review_id, 'note', text='Must roll back.')
        with self.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM staff_review_notes').fetchone()[0], 0)

    def test_full_http_workflow_access_csrf_history_summary_and_escaping(self):
        review_id = self.start()
        app = create_app({'TESTING': True, 'SECRET_KEY': token_urlsafe(48), 'DATABASE_URL': self.url})
        client = app.test_client()
        path = f'/assessments/{HOUSEHOLD_UUID}'
        record = path + f'/reviews/4Ps/{review_id}'
        data = {'csrf_token': 'test-token', 'batch_id': self.batch_id, 'run_id': self.run_id,
                'action': 'note', 'text': '<script>observed</script>', 'staff_id': self.staff_ids[0]}
        self.assertEqual(client.post(record, data=data).location, '/login')
        with client.session_transaction() as session:
            session.update(user_id=self.staff_ids[1], session_version=1,
                           issued_at=int(time()), csrf_token='test-token')
        for action in ('note', 'finish', 'cancel'):
            suffix = '' if action == 'note' else '?action=' + action
            self.assertEqual(client.post(record + suffix, data={**data, 'action': action,
                                                               'csrf_token': 'bad'}).status_code, 400)
        response = client.post(record + '?page=2&priority=both&program=SLP', data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('&lt;script&gt;observed&lt;/script&gt;', response.text)
        self.assertNotIn('<script>observed', response.text)
        self.assertIn('page=2', response.text)
        self.assertIn('program=SLP', response.text)
        self.assertEqual(response.text.count('<form'), 2)  # logout and Add Note
        for mode in ('finish', 'cancel'):
            page = client.get(record + '?action=' + mode)
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('id="review-note"', page.text)
        self.assertEqual(client.post(record + '?action=finish', data={**data, 'action': 'finish',
                              'recommendation': 'No Further Action', 'text': ''}).status_code, 400)
        response = client.post(record + '?action=finish', data={**data, 'action': 'finish',
                              'recommendation': 'No Further Action', 'text': 'Reviewed circumstances.'},
                               follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('No Further Action', response.text)
        self.assertNotIn('id="review-note"', response.text)
        self.assertEqual(client.post(record, data=data).status_code, 409)
        for tab in ('overview', 'mpi', 'screening', 'reviews'):
            page = client.get(path + '?tab=' + tab)
            self.assertEqual(page.status_code, 200)
            if tab == 'overview':
                self.assertIn('Staff Review Status', page.text)
                self.assertIn('View Staff Reviews', page.text)
            else:
                self.assertNotIn('id="review-status-heading"', page.text)
            if tab in ('overview', 'reviews'):
                self.assertIn('No Further Action', page.text)
        self.assertIn('View Subsidy Screening', client.get(path).text)
        self.assertIn('Review History', client.get(path + '?tab=reviews').text)
        self.assertEqual(client.get(path + f'/reviews/TUPAD/{review_id}').status_code, 404)
        second = self.start()
        self.assertEqual(client.post(path + f'/reviews/4Ps/{second}?action=cancel',
                                    data={**data, 'action': 'cancel', 'text': ''}).status_code, 303)
        with self.connect() as connection:
            rows = repository.list_history(connection, self.batch_id, HOUSEHOLD_UUID)
            self.assertEqual(rows[0]['notes'][0]['authored_by'], self.staff_ids[1])
            self.assertEqual(rows[0]['finished_by'], self.staff_ids[1])
            self.assertEqual(rows[1]['status'], 'Cancelled')
            self.assertIsNone(rows[1]['cancellation_reason'])
            self.assertEqual(self.snapshot(connection), self.originals)

    def test_migration_preserves_preexisting_active_review_and_allows_cancellation(self):
        schema = 'review_check_' + uuid4().hex
        with self.connect() as connection:
            connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            try:
                connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                paths = sorted((ROOT / 'migrations').glob('*.sql'))
                for path in paths:
                    if path.name < '0006':
                        connection.execute(path.read_text(encoding='utf-8'))
                staff_id = connection.execute(
                    "INSERT INTO staff_users (username, password_hash, role) VALUES ('synthetic', %s, 'lgu_staff') RETURNING id",
                    (generate_password_hash(token_urlsafe(24)),),
                ).fetchone()[0]
                batch, run = self.make_batch(connection, active=True)
                review_id = connection.execute(
                    """INSERT INTO staff_reviews
                       (batch_id, household_uuid, assessment_run_id, program, started_by)
                       VALUES (%s, %s, %s, 'TUPAD', %s) RETURNING id""",
                    (batch, HOUSEHOLD_UUID, run, staff_id),
                ).fetchone()[0]
                before = connection.execute('SELECT * FROM staff_reviews').fetchone()
                connection.execute((ROOT / 'migrations/0006_staff_review_completion.sql').read_text(encoding='utf-8'))
                after = connection.execute('SELECT * FROM staff_reviews').fetchone()
                self.assertEqual(after[:len(before)], before)
                repository.cancel(connection, review_id, staff_id, 'Started by mistake.')
                self.assertEqual(connection.execute('SELECT status FROM staff_reviews').fetchone()[0], 'Cancelled')
            finally:
                connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    unittest.main()
