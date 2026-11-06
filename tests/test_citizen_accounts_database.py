"""Migration 0014 and the citizen accounts repository, in isolated test schemas."""

from datetime import timedelta
import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from repositories import citizen_accounts as repository

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
PASSWORD_HASH = "scrypt:32768:8:1$salt$" + "d" * 32
LIFETIME = timedelta(hours=48)


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class CitizenAccountDatabaseTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.schema = f"citizen_check_{uuid4().hex}"
        self.connection = psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True)
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)),
        )
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.connection.execute(path.read_text("utf-8"))
        self._add_synthetic_households_and_staff()

    def _drop_schema(self):
        self.connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.close()

    def _add_synthetic_households_and_staff(self):
        """Two batches share one UUID to show that accounts belong to one batch."""
        for _ in range(2):
            self.connection.execute(
                "INSERT INTO dataset_batches (survey_year, municipality, source_columns)"
                " VALUES (2022, 'Pozorrubio', %s)", (Jsonb({}),),
            )
        self.batch, self.other_batch = [row[0] for row in self.connection.execute(
            "SELECT id FROM dataset_batches ORDER BY id").fetchall()]
        for batch, uuid, row in (
            (self.batch, "H-1", 1), (self.batch, "H-2", 2), (self.other_batch, "H-1", 1),
        ):
            self.connection.execute(
                "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                " VALUES (%s, %s, %s, %s)", (batch, uuid, row, Jsonb({})),
            )
        self.staff_id = self.connection.execute(
            "INSERT INTO staff_users (username, password_hash, role)"
            " VALUES ('synthetic_staff', 'scrypt:x$y$z', 'lgu_staff') RETURNING id",
        ).fetchone()[0]

    def _account(self, uuid="H-1", email="citizen@example.com", batch=None):
        return repository.insert_account(
            self.connection, batch or self.batch, uuid, "Synthetic Citizen", email,
        )

    def _token(self, account, purpose="activation", token_hash=HASH_A, code_hash=HASH_B):
        code_hash = code_hash if purpose == "activation" else None
        return repository.insert_token(
            self.connection, account["id"], purpose, token_hash, code_hash, LIFETIME,
        )

    def _refused(self, error, statement, parameters=()):
        with self.assertRaises(error):
            self.connection.execute(statement, parameters)

    # --- schema shape -------------------------------------------------------

    def test_migration_creates_the_three_tables_and_the_history_trigger(self):
        tables = {row[0] for row in self.connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s", (self.schema,))}
        self.assertTrue({"citizen_accounts", "citizen_account_tokens",
                         "citizen_account_events"} <= tables)
        triggers = {row[0] for row in self.connection.execute(
            "SELECT tgname FROM pg_trigger WHERE tgname = 'citizen_account_events_immutable'")}
        self.assertEqual(triggers, {"citizen_account_events_immutable"})

    # --- accounts: household link and uniqueness ----------------------------

    def test_account_starts_pending_without_a_password(self):
        account = self._account()
        self.assertEqual(account["status"], "pending_activation")
        self.assertEqual(account["session_version"], 1)
        self.assertIsNone(account["activated_at"])
        self.assertNotIn("password_hash", account)
        self.assertEqual(repository.find_by_email(self.connection, "citizen@example.com")
                         ["password_hash"], None)

    def test_account_must_link_to_an_existing_household_in_that_batch(self):
        for batch, uuid in ((self.batch, "UNKNOWN"), (self.other_batch, "H-2")):
            with self.subTest(batch=batch, uuid=uuid):
                with self.assertRaises(errors.ForeignKeyViolation):
                    self._account(uuid=uuid, batch=batch)

    def test_one_permanent_account_per_household_record(self):
        self._account(uuid="H-1")
        with self.assertRaises(errors.UniqueViolation):
            self._account(uuid="H-1", email="second@example.com")
        # A deactivated account still occupies the household (no replacements yet).
        account = repository.find_by_household(self.connection, self.batch, "H-1")
        repository.deactivate(self.connection, account["id"])
        with self.assertRaises(errors.UniqueViolation):
            self._account(uuid="H-1", email="third@example.com")

    def test_same_uuid_in_another_batch_is_a_separate_household_record(self):
        first = self._account(uuid="H-1", batch=self.batch)
        second = self._account(uuid="H-1", batch=self.other_batch, email="other@example.com")
        self.assertNotEqual(first["id"], second["id"])

    def test_email_is_unique_across_all_accounts_and_must_be_normalised(self):
        self._account(uuid="H-1", email="shared@example.com")
        with self.assertRaises(errors.UniqueViolation):
            self._account(uuid="H-2", email="shared@example.com")
        for email in ("Shared@Example.com", " spaced@example.com", "no-at-sign.example.com",
                      "a@b", "two@@example.com", "white space@example.com", "x@y.z",
                      "a" * 250 + "@example.com"):
            with self.subTest(email=email):
                with self.assertRaises(errors.CheckViolation):
                    self._account(uuid="H-2", email=email)

    def test_lookups_find_accounts_by_email_household_and_id(self):
        account = self._account()
        found = repository.find_by_email(self.connection, "citizen@example.com")
        self.assertEqual(found["id"], account["id"])
        self.assertIsNone(repository.find_by_email(self.connection, "none@example.com"))
        self.assertEqual(repository.find_by_household(
            self.connection, self.batch, "H-1")["id"], account["id"])
        self.assertIsNone(repository.find_by_household(self.connection, self.batch, "H-2"))
        self.assertEqual(repository.find_by_id(self.connection, account["id"])["email"],
                         "citizen@example.com")

    def test_name_and_password_hash_rules(self):
        for name in ("", "   ", "x" * 121):
            with self.subTest(name=name):
                with self.assertRaises(errors.CheckViolation):
                    repository.insert_account(
                        self.connection, self.batch, "H-1", name, "ok@example.com")
        account = self._account()
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_accounts SET password_hash = 'plain-text' WHERE id = %s",
                      (account["id"],))

    def test_status_and_password_must_agree(self):
        account = self._account()
        # Active needs a password and an activation time; pending cannot hold a password.
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_accounts SET status = 'active' WHERE id = %s",
                      (account["id"],))
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_accounts SET password_hash = %s WHERE id = %s",
                      (PASSWORD_HASH, account["id"]))
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_accounts SET status = 'closed' WHERE id = %s",
                      (account["id"],))

    # --- account lifecycle --------------------------------------------------

    def test_activation_sets_password_and_ends_old_sessions_only_from_pending(self):
        account = self._account()
        active = repository.activate(self.connection, account["id"], PASSWORD_HASH)
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["session_version"], 2)
        self.assertIsNotNone(active["activated_at"])
        self.assertEqual(repository.find_by_email(
            self.connection, "citizen@example.com")["password_hash"], PASSWORD_HASH)
        self.assertIsNone(repository.activate(self.connection, account["id"], PASSWORD_HASH))

    def test_password_replacement_works_for_active_accounts_only(self):
        account = self._account()
        self.assertIsNone(repository.replace_password(
            self.connection, account["id"], PASSWORD_HASH))
        repository.activate(self.connection, account["id"], PASSWORD_HASH)
        changed = repository.replace_password(
            self.connection, account["id"], PASSWORD_HASH.replace("d", "e"))
        self.assertEqual(changed["session_version"], 3)

    def test_returning_to_pending_clears_the_password_and_can_change_the_email(self):
        account = self._account()
        repository.activate(self.connection, account["id"], PASSWORD_HASH)
        pending = repository.return_to_pending(
            self.connection, account["id"], "new-address@example.com")
        self.assertEqual(pending["status"], "pending_activation")
        self.assertEqual(pending["email"], "new-address@example.com")
        self.assertEqual(pending["session_version"], 3)
        self.assertIsNone(repository.find_by_email(
            self.connection, "new-address@example.com")["password_hash"])
        same_email = repository.return_to_pending(self.connection, account["id"])
        self.assertEqual(same_email["email"], "new-address@example.com")

    def test_deactivation_ends_sessions_and_keeps_the_household_link(self):
        account = self._account()
        repository.activate(self.connection, account["id"], PASSWORD_HASH)
        blocked = repository.deactivate(self.connection, account["id"])
        self.assertEqual(blocked["status"], "deactivated")
        self.assertEqual(blocked["session_version"], 3)
        self.assertEqual((blocked["batch_id"], blocked["household_uuid"]), (self.batch, "H-1"))

    def test_failed_logins_lock_the_account_at_the_limit_and_success_resets(self):
        account = self._account()
        for expected in (1, 2, 3, 4):
            result = repository.record_failed_login(self.connection, account["id"], 5, 15)
            self.assertEqual(result["failed_login_count"], expected)
            self.assertIsNone(result["locked_until"])
        locked = repository.record_failed_login(self.connection, account["id"], 5, 15)
        self.assertEqual(locked["failed_login_count"], 5)
        minutes = self.connection.execute(
            "SELECT extract(epoch FROM locked_until - CURRENT_TIMESTAMP) / 60"
            " FROM citizen_accounts WHERE id = %s", (account["id"],)).fetchone()[0]
        self.assertAlmostEqual(float(minutes), 15, delta=0.5)
        repository.record_successful_login(self.connection, account["id"])
        reset = repository.find_by_id(self.connection, account["id"])
        self.assertEqual((reset["failed_login_count"], reset["locked_until"]), (0, None))
        self.assertIsNotNone(reset["last_login_at"])

    # --- tokens -------------------------------------------------------------

    def test_token_rules_for_activation_and_reset(self):
        account = self._account()
        insert = ("INSERT INTO citizen_account_tokens (account_id, purpose, token_hash,"
                  " code_hash, expires_at) VALUES (%s, %s, %s, %s, now() + interval '1 hour')")
        cases = (
            ("activation without a code", (account["id"], "activation", HASH_A, None)),
            ("reset with a code", (account["id"], "password_reset", HASH_A, HASH_B)),
            ("unknown purpose", (account["id"], "login", HASH_A, None)),
            ("token not a hash", (account["id"], "password_reset", "plain-token", None)),
            ("code not a hash", (account["id"], "activation", HASH_A, "123456")),
        )
        for label, parameters in cases:
            with self.subTest(label):
                with self.assertRaises(errors.CheckViolation):
                    self.connection.execute(insert, parameters)
        self._refused(errors.CheckViolation, insert.replace("now() + interval '1 hour'",
                                                            "now() - interval '1 hour'"),
                      (account["id"], "password_reset", HASH_A, None))
        self._token(account)
        self._token(account, "password_reset", HASH_C)

    def test_only_one_open_link_per_account_and_purpose(self):
        account = self._account()
        self._token(account)
        with self.assertRaises(errors.UniqueViolation):
            self._token(account, token_hash=HASH_C)
        self.assertEqual(repository.revoke_open_tokens(
            self.connection, account["id"], "activation"), 1)
        self._token(account, token_hash=HASH_C)

    def test_token_hash_is_unique_and_attempts_stay_within_five(self):
        first, second = self._account(uuid="H-1"), self._account(uuid="H-2", email="b@example.com")
        self._token(first)
        with self.assertRaises(errors.UniqueViolation):
            self._token(second)
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_account_tokens SET failed_code_attempts = 6")
        self._refused(errors.CheckViolation,
                      "UPDATE citizen_account_tokens SET used_at = now(), revoked_at = now()")

    def test_revoking_cancels_every_open_link_or_only_one_purpose(self):
        account = self._account()
        self._token(account)
        self._token(account, "password_reset", HASH_C)
        self.assertEqual(repository.revoke_open_tokens(
            self.connection, account["id"], "password_reset"), 1)
        self.assertIsNone(repository.find_open_token(self.connection, HASH_C, "password_reset"))
        self.assertIsNotNone(repository.find_open_token(self.connection, HASH_A, "activation"))
        self.assertEqual(repository.revoke_open_tokens(self.connection, account["id"]), 1)
        self.assertEqual(repository.revoke_open_tokens(self.connection, account["id"]), 0)

    def test_open_token_lookup_excludes_used_revoked_expired_and_wrong_purpose(self):
        account = self._account()
        token = self._token(account)
        found = repository.find_open_token(self.connection, HASH_A, "activation", lock=True)
        self.assertEqual(found["id"], token["id"])
        self.assertEqual(found["account_status"], "pending_activation")
        self.assertEqual((found["code_hash"], found["failed_code_attempts"]), (HASH_B, 0))
        self.assertIsNone(repository.find_open_token(self.connection, HASH_A, "password_reset"))
        self.assertIsNone(repository.find_open_token(self.connection, HASH_C, "activation"))
        self.connection.execute(
            "UPDATE citizen_account_tokens SET created_at = now() - interval '3 days',"
            " expires_at = now() - interval '1 day'")
        self.assertIsNone(repository.find_open_token(self.connection, HASH_A, "activation"))
        self.assertFalse(repository.consume_token(self.connection, token["id"]))

    def test_the_fifth_wrong_code_cancels_the_link_and_the_code_together(self):
        account = self._account()
        token = self._token(account)
        for attempt in (1, 2, 3, 4):
            result = repository.record_failed_code_attempt(
                self.connection, token["id"], 5)
            self.assertEqual((result["failed_code_attempts"], result["revoked"]),
                             (attempt, False))
        last = repository.record_failed_code_attempt(self.connection, token["id"], 5)
        self.assertEqual((last["failed_code_attempts"], last["revoked"]), (5, True))
        self.assertIsNone(repository.find_open_token(self.connection, HASH_A, "activation"))
        self.assertFalse(repository.consume_token(self.connection, token["id"]))
        self.assertIsNone(repository.record_failed_code_attempt(self.connection, token["id"], 5))

    def test_failed_attempts_are_kept_when_the_surrounding_request_fails(self):
        """A wrong code must count even though the activation itself did not succeed."""
        account = self._account()
        token = self._token(account)
        with self.connection.transaction():
            repository.record_failed_code_attempt(self.connection, token["id"], 5)
        found = repository.find_open_token(self.connection, HASH_A, "activation")
        self.assertEqual(found["failed_code_attempts"], 1)

    def test_a_link_can_be_consumed_only_once(self):
        account = self._account()
        token = self._token(account)
        self.assertTrue(repository.consume_token(self.connection, token["id"]))
        self.assertFalse(repository.consume_token(self.connection, token["id"]))
        self.assertIsNone(repository.find_open_token(self.connection, HASH_A, "activation"))
        # A used link no longer blocks a new one.
        self._token(account, token_hash=HASH_C)

    def test_email_acceptance_is_recorded_separately_from_delivery(self):
        account = self._account()
        token = self._token(account)
        self.assertIsNone(self.connection.execute(
            "SELECT email_accepted_at FROM citizen_account_tokens WHERE id = %s",
            (token["id"],)).fetchone()[0])
        repository.mark_email_accepted(self.connection, token["id"])
        self.assertIsNotNone(self.connection.execute(
            "SELECT email_accepted_at FROM citizen_account_tokens WHERE id = %s",
            (token["id"],)).fetchone()[0])

    def test_no_plain_token_or_code_column_exists(self):
        columns = {row[0] for row in self.connection.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = 'citizen_account_tokens'",
            (self.schema,))}
        self.assertEqual(columns, {
            "id", "account_id", "purpose", "token_hash", "code_hash", "failed_code_attempts",
            "expires_at", "used_at", "revoked_at", "email_accepted_at", "created_at"})

    # --- staff action history -----------------------------------------------

    def test_history_records_the_signed_in_staff_member_oldest_first(self):
        account = self._account()
        repository.insert_event(
            self.connection, account["id"], self.staff_id, "created", "Spoke with the representative.",
            representative_verified=True, privacy_consent_confirmed=True)
        repository.insert_event(
            self.connection, account["id"], self.staff_id, "deactivated", "Requested by the household.")
        history = repository.list_events(self.connection, account["id"])
        self.assertEqual([item["action"] for item in history], ["created", "deactivated"])
        self.assertEqual(history[0]["staff_username"], "synthetic_staff")
        self.assertEqual((history[0]["representative_verified"],
                          history[0]["privacy_consent_confirmed"]), (True, True))
        self.assertEqual((history[1]["representative_verified"],
                          history[1]["privacy_consent_confirmed"]), (False, False))

    def test_access_granting_actions_need_both_confirmations(self):
        account = self._account()
        for action in ("created", "email_changed", "reactivated"):
            for verified, consent in ((False, False), (True, False), (False, True)):
                with self.subTest(action=action, verified=verified, consent=consent):
                    with self.assertRaises(errors.CheckViolation):
                        repository.insert_event(
                            self.connection, account["id"], self.staff_id, action, "Note.",
                            representative_verified=verified,
                            privacy_consent_confirmed=consent)
            repository.insert_event(
                self.connection, account["id"], self.staff_id, action, "Note.",
                representative_verified=True, privacy_consent_confirmed=True)
        for action in ("activation_resent", "deactivated"):
            repository.insert_event(self.connection, account["id"], self.staff_id, action, "Note.")

    def test_history_needs_a_known_action_note_and_staff_member(self):
        account = self._account()
        for staff, action, note in ((self.staff_id, "deleted", "Note."),
                                    (self.staff_id, "deactivated", "   "),
                                    (self.staff_id, "deactivated", "x" * 501)):
            with self.subTest(action=action, note=note[:5]):
                with self.assertRaises(errors.CheckViolation):
                    repository.insert_event(self.connection, account["id"], staff, action, note)
        with self.assertRaises(errors.ForeignKeyViolation):
            repository.insert_event(
                self.connection, account["id"], self.staff_id + 99, "deactivated", "Note.")

    def test_history_cannot_be_changed_or_removed(self):
        account = self._account()
        repository.insert_event(self.connection, account["id"], self.staff_id,
                                "deactivated", "Requested by the household.")
        self._refused(psycopg.errors.RaiseException,
                      "UPDATE citizen_account_events SET note = 'edited'")
        self._refused(psycopg.errors.RaiseException, "DELETE FROM citizen_account_events")

    # --- existing data is untouched ----------------------------------------

    def test_survey_households_and_staff_reviews_are_not_changed_by_accounts(self):
        before = self.connection.execute(
            "SELECT batch_id, uuid, responses FROM households ORDER BY 1, 2").fetchall()
        account = self._account()
        repository.activate(self.connection, account["id"], PASSWORD_HASH)
        self.assertEqual(before, self.connection.execute(
            "SELECT batch_id, uuid, responses FROM households ORDER BY 1, 2").fetchall())
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM staff_reviews").fetchone()[0], 0)

    def test_repository_never_reads_survey_answers(self):
        source = (ROOT / "repositories/citizen_accounts.py").read_text(encoding="utf-8")
        for table in ("households", "household_members", "member_health",
                      "household_assessments", "assessment_runs", "responses"):
            with self.subTest(table=table):
                self.assertNotRegex(source, rf"(FROM|JOIN)\s+{table}\b")


if __name__ == "__main__":
    unittest.main()
