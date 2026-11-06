"""Synthetic PostgreSQL checks for atomic activation and switch-back."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from werkzeug.security import generate_password_hash

from repositories import assessment_runs
from services.assessments import build_unified_assessments
from services.batch_activation import (
    BatchActivationError, activate_prepared_batch, activation_overview,
)
from services.development_data import development_sources


ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic checks.")
class BatchActivationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = conninfo_to_dict(TEST_URL)
        if (settings.get("dbname") != "tanglaw_buhay_test"
                or settings.get("host") not in ("localhost", "127.0.0.1", "::1")
                or settings.get("hostaddr", "127.0.0.1") not in ("127.0.0.1", "::1")
                or "service" in settings):
            raise ValueError("Synthetic checks require the local test database.")
        datasets, references = development_sources()
        cls.findings = build_unified_assessments(
            datasets, references, survey_year=2022,
        )

    def setUp(self):
        self.schema = f"switch_check_{uuid4().hex}"
        self.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)),
            )
        self.addCleanup(self._drop_schema)
        with psycopg.connect(self.url) as connection:
            for name in (
                "0001_database_foundation.sql",
                "0002_staff_authentication.sql",
                "0003_assessment_persistence.sql",
                "0004_assessment_run_selection.sql",
                "0005_staff_reviews.sql",
                "0006_staff_review_completion.sql",
                "0014_batch_activation_audit.sql",
            ):
                connection.execute((ROOT / "migrations" / name).read_text())
            self.admin_id = connection.execute(
                """INSERT INTO staff_users (username, password_hash, role)
                   VALUES ('synthetic_admin', %s, 'system_administrator')
                   RETURNING id""",
                (generate_password_hash("synthetic-admin-password", method="scrypt"),),
            ).fetchone()[0]
            self.staff_id = connection.execute(
                """INSERT INTO staff_users (username, password_hash, role)
                   VALUES ('synthetic_staff', %s, 'lgu_staff')
                   RETURNING id""",
                (generate_password_hash("synthetic-staff-password", method="scrypt"),),
            ).fetchone()[0]
            self.old_batch, self.old_run = self._batch(connection, active=True)
            self.new_batch, self.new_run = self._batch(connection, active=False)

    def _drop_schema(self):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
            )

    def _factory(self):
        return psycopg.connect(self.url)

    def _batch(self, connection, *, active):
        source = "controlled_2022_pilot" if active else "administrator_uploaded_compatible_csv"
        batch_id = connection.execute(
            """INSERT INTO dataset_batches
               (survey_year, municipality, source_columns, source_provenance,
                validation_status, is_active)
               VALUES (2022, 'Pozorrubio', %s, %s, 'validated', %s)
               RETURNING id""",
            (Jsonb({"household": ["uuid"]}), Jsonb({"declared": {"source": source}}),
             active),
        ).fetchone()[0]
        with connection.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO households (batch_id, uuid, source_row_number, responses)
                   VALUES (%s, %s, %s, %s)""",
                ((batch_id, household_uuid, position, Jsonb({"uuid": household_uuid}))
                 for position, household_uuid in enumerate(self.findings, 1)),
            )
        run_id = assessment_runs.create_run(
            connection, batch_id, calculation_version="synthetic-current-v1",
            calculation_provenance={"source": "synthetic_test"},
        )
        assessment_runs.store_household_assessments(
            connection, run_id, self.findings,
        )
        assessment_runs.complete_run(connection, run_id)
        assessment_runs.designate_run(connection, batch_id, run_id)
        return batch_id, run_id

    def _switch(self, target, *, actor_id=None, current=None, current_run=None,
                target_run=None):
        return activate_prepared_batch(
            target, self.admin_id if actor_id is None else actor_id,
            expected_active_batch_id=self.old_batch if current is None else current,
            expected_active_run_id=self.old_run if current_run is None else current_run,
            expected_target_run_id=self.new_run if target_run is None else target_run,
            connection_factory=self._factory,
        )

    def _active_batch(self):
        with self._factory() as connection:
            return connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0]

    def test_activation_and_switch_back_keep_runs_and_reviews(self):
        overview = activation_overview(connection_factory=self._factory)
        self.assertEqual(overview["active"]["id"], self.old_batch)
        self.assertEqual(overview["candidates"][0]["id"], self.new_batch)
        self.assertEqual(overview["in_progress_reviews"], 0)
        first = self._switch(self.new_batch)
        self.assertEqual(first["action"], "activate")
        self.assertEqual(self._active_batch(), self.new_batch)
        returned = self._switch(
            self.old_batch, current=self.new_batch,
            current_run=self.new_run, target_run=self.old_run,
        )
        self.assertEqual(returned["action"], "switch_back")
        self.assertEqual(self._active_batch(), self.old_batch)
        with self._factory() as connection:
            self.assertEqual(connection.execute(
                """SELECT action, previous_batch_id, target_batch_id,
                          previous_run_id, target_run_id, active_reviews_at_switch
                   FROM batch_activation_events ORDER BY id""",
            ).fetchall(), [
                ("activate", self.old_batch, self.new_batch,
                 self.old_run, self.new_run, 0),
                ("switch_back", self.new_batch, self.old_batch,
                 self.new_run, self.old_run, 0),
            ])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM assessment_runs",
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM household_assessments",
            ).fetchone()[0], 2 * len(self.findings))

    def test_unfinished_reviews_block_switch_until_cancelled(self):
        household_uuid = next(iter(self.findings))
        with self._factory() as connection:
            review_id = connection.execute(
                """INSERT INTO staff_reviews
                   (batch_id, household_uuid, assessment_run_id, program, started_by)
                   VALUES (%s, %s, %s, '4Ps', %s) RETURNING id""",
                (self.old_batch, household_uuid, self.old_run, self.staff_id),
            ).fetchone()[0]
        with self.assertRaisesRegex(BatchActivationError, "unfinished_reviews") as blocked:
            self._switch(self.new_batch)
        self.assertEqual(blocked.exception.review_count, 1)
        self.assertEqual(self._active_batch(), self.old_batch)
        with self._factory() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM batch_activation_events",
            ).fetchone()[0], 0)
            connection.execute(
                """UPDATE staff_reviews SET status = 'Cancelled',
                   cancelled_by = %s, cancelled_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (self.staff_id, review_id),
            )
        self._switch(self.new_batch)
        with self._factory() as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM staff_reviews WHERE id = %s", (review_id,),
            ).fetchone()[0], "Cancelled")

    def test_stale_selection_incomplete_run_and_non_admin_fail_closed(self):
        with self.assertRaisesRegex(BatchActivationError, "unauthorized"):
            self._switch(self.new_batch, actor_id=self.staff_id)
        with self.assertRaisesRegex(BatchActivationError, "selection_changed"):
            self._switch(self.new_batch, current_run=self.new_run)
        with self._factory() as connection:
            connection.execute(
                """DELETE FROM household_assessments
                   WHERE run_id = %s AND household_uuid = %s""",
                (self.new_run, next(iter(self.findings))),
            )
        with self.assertRaisesRegex(BatchActivationError, "batch_not_ready"):
            self._switch(self.new_batch)
        self.assertEqual(self._active_batch(), self.old_batch)

    def test_audit_failure_rolls_back_pointer(self):
        with patch("services.batch_activation.batch_activation_events.record_switch",
                   side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaisesRegex(BatchActivationError, "activation_unavailable"):
                self._switch(self.new_batch)
        self.assertEqual(self._active_batch(), self.old_batch)
        with self._factory() as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM batch_activation_events",
            ).fetchone()[0], 0)
