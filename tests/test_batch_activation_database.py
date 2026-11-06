"""Synthetic PostgreSQL checks for atomic activation and switch-back."""

import os
from pathlib import Path
from secrets import token_urlsafe
from threading import Event, Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import g
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from werkzeug.security import generate_password_hash

from app import create_app
from repositories import assessment_runs, dataset_batches, staff_reviews
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

    # ---- Citizen Accounts / Document Requests safeguard (temporary, single-year rule)

    OPEN_STEPS = {
        "requested": [], "submitted": ["submitted"],
        "needs_correction": ["submitted", "needs_correction"],
        "accepted": ["submitted", "accepted"], "cancelled": ["cancelled"],
    }

    def _add_citizen_tables(self, *, documents=True):
        """Create the optional citizen tables inside this test schema only."""
        names = ["0016_citizen_accounts.sql"]
        if documents:
            names.append("0017_document_requests.sql")
        with self._factory() as connection:
            for name in names:
                connection.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))

    def _account(self, connection, batch_id, status, number):
        """Insert one synthetic Citizen Account directly in the given status."""
        household_uuid = list(self.findings)[number]
        password_hash = "scrypt:1$synthetic$synthetic" if status == "active" else None
        return connection.execute(
            """INSERT INTO citizen_accounts
               (batch_id, household_uuid, representative_name, email, password_hash,
                status, activated_at)
               VALUES (%s, %s, 'Synthetic Person', %s, %s, %s,
                       CASE WHEN %s THEN CURRENT_TIMESTAMP END)
               RETURNING id""",
            (batch_id, household_uuid, f"synthetic{number}@example.test", password_hash,
             status, status == "active"),
        ).fetchone()[0]

    def _request(self, connection, account_id, final_status):
        """Insert a document request and move it along the allowed steps."""
        request_id = connection.execute(
            """INSERT INTO document_requests (account_id, title, requested_by)
               VALUES (%s, 'Synthetic document', %s) RETURNING id""",
            (account_id, self.staff_id),
        ).fetchone()[0]
        for step in self.OPEN_STEPS[final_status]:
            connection.execute(
                "UPDATE document_requests SET status = %s WHERE id = %s", (step, request_id),
            )
        return request_id

    def _citizen_snapshot(self, *, documents=True):
        """Everything a blocked switch must leave exactly as it was."""
        with self._factory() as connection:
            return (
                connection.execute(
                    """SELECT id, batch_id, household_uuid, status, session_version
                       FROM citizen_accounts ORDER BY id""").fetchall(),
                connection.execute(
                    "SELECT id, account_id, status FROM document_requests ORDER BY id",
                ).fetchall() if documents else None,
                connection.execute(
                    "SELECT count(*) FROM batch_activation_events").fetchone()[0],
                connection.execute(
                    "SELECT id FROM dataset_batches WHERE is_active").fetchone()[0],
            )

    def _assert_switch_blocked(self, accounts, requests, *, documents=True, **switch):
        """The switch is refused with these counts and nothing at all is written."""
        before = self._citizen_snapshot(documents=documents)
        with self.assertRaisesRegex(BatchActivationError, "citizen_work_open") as blocked:
            self._switch(self.new_batch, **switch)
        self.assertEqual(
            (blocked.exception.account_count, blocked.exception.request_count),
            (accounts, requests),
        )
        self.assertEqual(self._citizen_snapshot(documents=documents), before)

    def test_pending_activation_account_blocks_switch(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            self._account(connection, self.old_batch, "pending_activation", 0)
        self._assert_switch_blocked(1, 0)

    def test_active_account_blocks_switch(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            self._account(connection, self.old_batch, "active", 0)
        self._assert_switch_blocked(1, 0)

    def test_open_requests_block_switch_even_for_a_deactivated_account(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            account = self._account(connection, self.old_batch, "deactivated", 0)
        for status in ("requested", "submitted", "needs_correction"):
            with self.subTest(status=status):
                with self._factory() as connection:
                    request_id = self._request(connection, account, status)
                self._assert_switch_blocked(0, 1)
                with self._factory() as connection:
                    connection.execute(
                        "UPDATE document_requests SET status = 'cancelled' WHERE id = %s",
                        (request_id,),
                    )

    def test_deactivated_account_and_finished_requests_do_not_block(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            account = self._account(connection, self.old_batch, "deactivated", 0)
            self._request(connection, account, "accepted")
            self._request(connection, account, "cancelled")
        before = self._citizen_snapshot()
        self.assertEqual(self._switch(self.new_batch)["action"], "activate")
        after = self._citizen_snapshot()
        # Accounts and requests are untouched; only the active batch and audit differ.
        self.assertEqual(after[:2], before[:2])
        self.assertEqual(after[3], self.new_batch)

    def test_switch_is_allowed_once_staff_resolve_the_citizen_records(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            account = self._account(connection, self.old_batch, "active", 0)
            request_id = self._request(connection, account, "submitted")
        self._assert_switch_blocked(1, 1)
        with self._factory() as connection:
            connection.execute(
                "UPDATE document_requests SET status = 'accepted' WHERE id = %s",
                (request_id,),
            )
            connection.execute(
                "UPDATE citizen_accounts SET status = 'deactivated' WHERE id = %s",
                (account,),
            )
        self.assertEqual(self._switch(self.new_batch)["action"], "activate")

    def test_unfinished_reviews_guard_still_comes_first(self):
        self._add_citizen_tables()
        household_uuid = next(iter(self.findings))
        with self._factory() as connection:
            self._account(connection, self.old_batch, "pending_activation", 1)
            connection.execute(
                """INSERT INTO staff_reviews
                   (batch_id, household_uuid, assessment_run_id, program, started_by)
                   VALUES (%s, %s, %s, '4Ps', %s)""",
                (self.old_batch, household_uuid, self.old_run, self.staff_id),
            )
        with self.assertRaisesRegex(BatchActivationError, "unfinished_reviews"):
            self._switch(self.new_batch)

    def test_switch_back_follows_the_same_rules(self):
        self._add_citizen_tables()
        self._switch(self.new_batch)
        with self._factory() as connection:
            account = self._account(connection, self.new_batch, "active", 0)
        back = {"current": self.new_batch, "current_run": self.new_run,
                "target_run": self.old_run}
        before = self._citizen_snapshot()
        with self.assertRaisesRegex(BatchActivationError, "citizen_work_open"):
            self._switch(self.old_batch, **back)
        self.assertEqual(self._citizen_snapshot(), before)
        with self._factory() as connection:
            connection.execute(
                "UPDATE citizen_accounts SET status = 'deactivated' WHERE id = %s",
                (account,),
            )
        self.assertEqual(self._switch(self.old_batch, **back)["action"], "switch_back")

    def test_citizen_records_in_other_batches_do_not_block(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            self._account(connection, self.new_batch, "active", 0)
        self.assertEqual(self._switch(self.new_batch)["action"], "activate")

    def test_missing_citizen_tables_are_treated_as_nothing_to_count(self):
        with self._factory() as connection:
            self.assertEqual(
                dataset_batches.count_blocking_citizen_work(connection, self.old_batch),
                {"accounts": 0, "requests": 0},
            )
        self.assertEqual(self._switch(self.new_batch)["action"], "activate")

    def test_missing_document_tables_never_hide_existing_accounts(self):
        self._add_citizen_tables(documents=False)
        with self._factory() as connection:
            self._account(connection, self.old_batch, "active", 0)
            self.assertEqual(
                dataset_batches.count_blocking_citizen_work(connection, self.old_batch),
                {"accounts": 1, "requests": 0},
            )
        self._assert_switch_blocked(1, 0, documents=False)

    def test_overview_reports_the_blocking_counts_in_plain_words(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            first = self._account(connection, self.old_batch, "active", 0)
            self._account(connection, self.old_batch, "pending_activation", 1)
            self._request(connection, first, "submitted")
        overview = activation_overview(connection_factory=self._factory)
        self.assertEqual(overview["citizen_accounts"], 2)
        self.assertEqual(overview["open_document_requests"], 1)
        self.assertEqual(
            overview["citizen_work_summary"],
            "2 citizen accounts that are active or waiting for activation "
            "and 1 unfinished document request",
        )

    def test_direct_post_cannot_bypass_the_safeguard(self):
        self._add_citizen_tables()
        with self._factory() as connection:
            account = self._account(connection, self.old_batch, "active", 0)
            self._request(connection, account, "submitted")
        app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": self.url,
        })
        client = app.test_client()

        def load_admin():
            g.user = {"id": self.admin_id, "username": "synthetic_admin",
                      "role": "system_administrator"}

        form = {
            "csrf_token": "synthetic-csrf", "target_batch_id": str(self.new_batch),
            "expected_active_batch_id": str(self.old_batch),
            "expected_active_run_id": str(self.old_run),
            "expected_target_run_id": str(self.new_run), "confirm_switch": "yes",
        }
        before = self._citizen_snapshot()
        with patch("services.auth.authentication_configured", return_value=True), \
                patch("services.auth.load_session_user", side_effect=load_admin):
            with client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            response = client.post("/administration/import/activation", data=form)
            page = client.get("/administration/import/activation")
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"1 citizen account that is active or waiting for activation "
                      b"and 1 unfinished document request", response.data)
        self.assertEqual(self._citizen_snapshot(), before)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Citizen accounts and document requests need attention", page.data)
        self.assertNotIn(b"Switch to Batch", page.data)

    def test_account_added_during_the_switch_is_counted(self):
        """A staff action holding the batch lock finishes first, so the check sees it."""
        self._add_citizen_tables(documents=False)
        outcome = {}

        def run_switch():
            try:
                self._switch(self.new_batch)
            except BatchActivationError as error:
                outcome["error"] = str(error)

        with self._factory() as staff_action:
            with staff_action.transaction():
                self.assertTrue(staff_reviews.lock_selected_context(
                    staff_action, self.old_batch, self.old_run))
                self._account(staff_action, self.old_batch, "pending_activation", 0)
                switch = Thread(target=run_switch)
                switch.start()
                switch.join(1.0)
                self.assertTrue(switch.is_alive(), "the switch should wait for the lock")
        switch.join(20)
        self.assertFalse(switch.is_alive())
        self.assertEqual(outcome.get("error"), "citizen_work_open")
        self.assertEqual(self._active_batch(), self.old_batch)

    def test_staff_action_started_during_the_switch_waits_and_then_fails(self):
        """Creating or reactivating an account cannot begin while a switch is deciding."""
        self._add_citizen_tables(documents=False)
        real_count = dataset_batches.count_blocking_citizen_work
        holding, release = Event(), Event()
        results = {}

        def slow_count(connection, batch_id):
            holding.set()
            release.wait(20)
            return real_count(connection, batch_id)

        def run_switch():
            results["switch"] = self._switch(self.new_batch)

        def staff_action():
            with self._factory() as connection, connection.transaction():
                results["context_locked"] = staff_reviews.lock_selected_context(
                    connection, self.old_batch, self.old_run)

        with patch.object(dataset_batches, "count_blocking_citizen_work",
                          side_effect=slow_count):
            switch = Thread(target=run_switch)
            switch.start()
            self.assertTrue(holding.wait(10))
            staff = Thread(target=staff_action)
            staff.start()
            staff.join(1.0)
            self.assertTrue(staff.is_alive(), "the staff action should wait for the switch")
            release.set()
            switch.join(20)
            staff.join(20)
        self.assertEqual(results["switch"]["action"], "activate")
        self.assertIs(results["context_locked"], False)
        self.assertEqual(self._active_batch(), self.new_batch)
