"""Reset validation uses the existing guarded synthetic schema, never development data."""

from pathlib import Path
import json
import os
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from psycopg.conninfo import conninfo_to_dict

from scripts import reset_staff_reviews as reset
from services import recommendation_changes
from assessment_detail_fixtures import HOUSEHOLD_UUID
import test_staff_reviews as reviews


class ResetTargetTests(unittest.TestCase):
    def test_rejects_wrong_remote_ambiguous_and_environment_targets(self):
        for url in ('', 'dbname=tanglaw_buhay_test host=localhost user=staff',
                    'dbname=production host=localhost user=staff',
                    'dbname=tanglaw_buhay_dev host=example.com user=staff',
                    'dbname=tanglaw_buhay_dev host=localhost hostaddr=192.0.2.1 user=staff',
                    'dbname=tanglaw_buhay_dev host=localhost,127.0.0.1 user=staff',
                    'dbname=tanglaw_buhay_dev host=localhost options=-csearch_path=other user=staff',
                    'dbname=tanglaw_buhay_dev service=other user=staff',
                    'dbname=tanglaw_buhay_dev host=localhost port=5432,5433 user=staff'):
            with self.subTest(url=url), self.assertRaises(reset.ResetError):
                reset.local_settings(url, {})
        good = 'dbname=tanglaw_buhay_dev host=localhost user=staff'
        for environment in ({'PGSERVICE': 'other'}, {'PGDATABASE': 'other'}, {'PGOPTIONS': '-c...'}):
            with self.assertRaises(reset.ResetError):
                reset.local_settings(good, environment)
        self.assertEqual(reset.local_settings(good, {})['hostaddr'], '127.0.0.1')
        with patch.object(reset.sys.stdin, 'isatty', return_value=False), \
                patch.object(reset.psycopg, 'connect') as connect:
            self.assertEqual(reset.main(), 1)
            connect.assert_not_called()


@unittest.skipUnless(reviews.TEST_URL, 'Set the isolated local test database URL.')
class ResetDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.fx = reviews.StaffReviewDatabaseTests()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.connect = self.fx.connect
        self.temporary = TemporaryDirectory(prefix='tanglaw_synthetic_review_backup_')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.settings = conninfo_to_dict(self.fx.url)
        self.settings.pop('options', None)
        with self.connect() as connection:
            self.tools = reset.postgres_tools(connection.info.server_version)
        review_id = self.fx.start()
        self.fx.act(review_id, 'note', text='Synthetic note to preserve in backup.')
        self.fx.act(review_id, 'reassign', responsible_staff_id=self.fx.staff_ids[1],
                    responsibility_version=1, text='Synthetic reassignment.')
        self.fx.act(review_id, 'finish', recommendation='For LGU Follow-Up', text='Original.')
        recommendation_changes.change_recommendation(
            HOUSEHOLD_UUID, '4Ps', review_id, 'withdraw', self.fx.staff_ids[1],
            self.fx.batch_id, self.fx.run_id, version=0, reason='Synthetic withdrawal.',
            connection_factory=self.connect,
        )
        with self.connect() as connection:
            self.before = {table: connection.execute(
                f'SELECT * FROM {table} ORDER BY id').fetchall() for table in reset.TABLES}
            self.accounts = connection.execute('SELECT * FROM staff_users ORDER BY id').fetchall()
            self.objects = self.schema_objects(connection)

    def schema_objects(self, connection):
        return connection.execute(
            """SELECT relname, relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=%s ORDER BY relname""", (self.fx.schema,),
        ).fetchall()

    def run_reset(self, *, confirm=lambda _: 'wrong', output=lambda _: None):
        with self.connect() as connection:
            return reset._reset_workflow(connection, self.fx.schema, self.settings,
                                         self.directory, self.tools, confirm=confirm, output=output)

    def assert_preserved(self):
        with self.connect() as connection:
            for table, rows in self.before.items():
                self.assertEqual(connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall(), rows)
            self.assertEqual(self.fx.snapshot(connection), self.fx.originals)
            self.assertEqual(connection.execute('SELECT * FROM staff_users ORDER BY id').fetchall(), self.accounts)

    def test_identity_guard_rejects_actual_isolated_test_database(self):
        with self.connect() as connection, self.assertRaises(reset.ResetError):
            reset.verify_target(connection, self.settings)

    def test_backup_failure_aborts_before_confirmation_and_keeps_all_records(self):
        with patch.object(reset.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pg_dump')), \
                patch('builtins.input') as confirm, self.assertRaises(reset.ResetError):
            self.run_reset(confirm=confirm)
        confirm.assert_not_called()
        self.assert_preserved()

    def test_failed_archive_verification_aborts_before_confirmation(self):
        original = reset.subprocess.run

        def run(arguments, **kwargs):
            if '--list' in arguments:
                return subprocess.CompletedProcess(arguments, 0, stdout='unrelated archive')
            return original(arguments, **kwargs)
        with patch.object(reset.subprocess, 'run', side_effect=run), self.assertRaises(reset.ResetError):
            self.run_reset(confirm=lambda _: self.fail('Unverified backup prompted confirmation'))
        self.assert_preserved()

    def test_required_confirmation_keeps_records_and_retains_verified_backup(self):
        messages = []
        prompts = []
        with self.assertRaises(reset.ResetError):
            self.run_reset(confirm=lambda prompt: prompts.append(prompt) or 'yes', output=messages.append)
        self.assertIn('RESET tanglaw_buhay_test DISPOSABLE REVIEWS ONLY', prompts[0])
        self.assertIn('staff_reviews=1', messages[0])
        self.assertIn('staff_review_notes=1', messages[0])
        self.assertEqual(len(list(self.directory.glob('*.dump'))), 1)
        self.assert_preserved()

    def test_successful_reset_has_recoverable_backup_and_preserves_all_nonreview_data(self):
        phrase = 'RESET tanglaw_buhay_test DISPOSABLE REVIEWS ONLY'
        archive, counts = self.run_reset(confirm=lambda _: phrase)
        self.assertEqual(counts, dict.fromkeys(reset.TABLES, 1))
        metadata = json.loads(archive.with_suffix('.json').read_text())
        self.assertEqual(metadata['counts'], counts)
        self.assertEqual(len(metadata['sha256']), 64)
        with self.connect() as connection:
            self.assertEqual(reset._counts(connection, self.fx.schema), dict.fromkeys(reset.TABLES, 0))
            self.assertEqual(self.fx.snapshot(connection), self.fx.originals)
            self.assertEqual(connection.execute('SELECT * FROM staff_users ORDER BY id').fetchall(), self.accounts)
            self.assertEqual(self.schema_objects(connection), self.objects)
        # Exercise a real data-only restore against the same disposable empty schema.
        environment = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
        environment.update(PGHOST=self.settings['host'], PGHOSTADDR=self.settings['hostaddr'],
                           PGDATABASE=self.settings['dbname'], PGUSER=self.settings['user'],
                           PGPASSWORD=self.settings.get('password', ''),
                           PGPORT=self.settings.get('port', '5432'))
        subprocess.run([self.tools[1], '--no-password', '--dbname=' + self.settings['dbname'],
                        '--exit-on-error', '--single-transaction', str(archive)],
                       env=environment, check=True, capture_output=True, timeout=30)
        self.assert_preserved()

    def test_failure_after_truncate_rolls_back_all_review_tables(self):
        original = reset._counts
        calls = 0

        def count(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise reset.ResetError('Synthetic verification failure after truncate.')
            return original(*args)
        with patch.object(reset, '_counts', side_effect=count), self.assertRaises(reset.ResetError):
            self.run_reset(confirm=lambda _: 'RESET tanglaw_buhay_test DISPOSABLE REVIEWS ONLY')
        self.assert_preserved()
