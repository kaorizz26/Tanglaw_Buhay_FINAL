"""Staff account creation and resend, on isolated schemas with email delivery mocked."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from repositories import citizen_accounts as repository
from services import assessment_queries
from services import citizen_accounts as service
from services.assessment_queries import AssessmentQueryError
from services.citizen_account_panel import PANEL_UNAVAILABLE, present_account_panel
from services.citizen_credentials import activation_code_matches, hash_token
from services.email_delivery import EmailDeliveryError

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
KEY = "synthetic-test-key-" + "k" * 24
HOUSEHOLD = "00000000-0000-4000-8000-000000000001"
NO_ASSESSMENT = "00000000-0000-4000-8000-000000000002"
NOTE = "Spoke with the representative by phone on the afternoon of the call."


def valid_form(**changes):
    form = {
        "representative_name": "  Synthetic   Citizen ", "email": " Citizen@Example.com ",
        "email_confirmation": "citizen@example.com", "representative_verified": "on",
        "privacy_consent_confirmed": "on", "verification_note": NOTE,
    }
    form.update(changes)
    return {key: value for key, value in form.items() if value is not None}


class FormValidationTests(unittest.TestCase):
    def test_a_valid_form_is_cleaned(self):
        values = service.validate_creation_form(valid_form())
        self.assertEqual(values, {
            "representative_name": "Synthetic Citizen", "email": "citizen@example.com",
            "verification_note": NOTE,
        })

    def test_each_problem_gets_its_own_plain_message(self):
        cases = {
            "representative_name": [{"representative_name": ""}, {"representative_name": "  "},
                                    {"representative_name": "x" * 121},
                                    {"representative_name": "Bad\x00Name"}],
            "email": [{"email": ""}, {"email": "no-at-sign"}, {"email": "a@b"},
                      {"email": "two@@example.com"}, {"email": "white space@example.com"},
                      {"email": "ünï@example.com"}, {"email": "a" * 250 + "@example.com"}],
            "email_confirmation": [{"email_confirmation": ""},
                                   {"email_confirmation": "other@example.com"}],
            "representative_verified": [{"representative_verified": None},
                                        {"representative_verified": "yes"}],
            "privacy_consent_confirmed": [{"privacy_consent_confirmed": None},
                                          {"privacy_consent_confirmed": "true"}],
            "verification_note": [{"verification_note": ""}, {"verification_note": "short"},
                                  {"verification_note": "x" * 501},
                                  {"verification_note": "ok" * 5 + "\x00"}],
        }
        for field, variants in cases.items():
            for change in variants:
                with self.subTest(field=field, change=str(change)[:40]):
                    with self.assertRaises(service.CitizenAccountInputError) as caught:
                        service.validate_creation_form(valid_form(**change))
                    self.assertIn(field, caught.exception.errors)
                    self.assertTrue(caught.exception.errors[field].endswith((".", ")")))

    def test_all_problems_are_reported_together(self):
        with self.assertRaises(service.CitizenAccountInputError) as caught:
            service.validate_creation_form({})
        self.assertEqual(set(caught.exception.errors), {
            "representative_name", "email", "representative_verified",
            "privacy_consent_confirmed", "verification_note"})

    def test_the_confirmation_is_compared_ignoring_case_and_spaces(self):
        values = service.validate_creation_form(valid_form(email_confirmation="  CITIZEN@example.COM "))
        self.assertEqual(values["email"], "citizen@example.com")

    def test_the_code_is_grouped_for_reading_aloud(self):
        self.assertEqual(service.format_activation_code("048213"), "048 213")


class PanelPresenterTests(unittest.TestCase):
    ROW = {"status": "pending_activation", "representative_name": "Synthetic Citizen",
           "email": "citizen@example.com", "created_at": "c", "activated_at": None,
           "email_accepted_at": None, "link_valid": True, "expires_at": "e"}

    def test_each_state_is_described(self):
        self.assertEqual(present_account_panel(None), {"state": "none"})
        self.assertEqual(present_account_panel(PANEL_UNAVAILABLE), {"state": "unavailable"})
        pending = present_account_panel(self.ROW)
        self.assertEqual((pending["state"], pending["status_label"], pending["can_resend"]),
                         ("pending_activation", "Pending Activation", True))
        for status, label in (("active", "Active"), ("deactivated", "Deactivated")):
            shown = present_account_panel({**self.ROW, "status": status})
            self.assertEqual((shown["status_label"], shown["can_resend"]), (label, False))

    def test_the_presented_panel_never_contains_credentials(self):
        text = json.dumps(present_account_panel(self.ROW), default=str)
        for word in ("token", "code_hash", "password", "link"):
            self.assertNotIn(word, text.replace("link_valid", ""))


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic account tests.")
class AccountCreationTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.schema = f"citizen_create_{uuid4().hex}"
        self.connection = psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True)
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)),
        )
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.connection.execute(path.read_text("utf-8"))
        self._seed()
        app = Flask(__name__)
        app.config["CITIZEN_CREDENTIAL_KEY"] = KEY
        self.app = app
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.sent = []
        patcher = patch.object(service, "send_activation_email", side_effect=self._record_send)
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def _drop_schema(self):
        self.connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.close()

    def _connect(self):
        return psycopg.connect(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={self.schema}",
        )

    def _seed(self):
        """One active validated batch, a designated run, two households and staff."""
        execute = self.connection.execute
        self.batch = execute(
            "INSERT INTO dataset_batches (survey_year, municipality, source_columns,"
            " source_provenance, validation_status, is_active)"
            " VALUES (2022, 'Pozorrubio', %s, %s, 'validated', true) RETURNING id",
            (Jsonb({}), Jsonb({"kind": "synthetic"})),
        ).fetchone()[0]
        for number, uuid in enumerate((HOUSEHOLD, NO_ASSESSMENT), 1):
            execute("INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                    " VALUES (%s, %s, %s, %s)",
                    (self.batch, uuid, number,
                     Jsonb({"barangay": "Synthetic Barangay", "contact_number": "09123456789"})))
        execute("INSERT INTO household_members (batch_id, uuid, line_number, source_row_number,"
                " responses) VALUES (%s, %s, 1, 1, %s)",
                (self.batch, HOUSEHOLD, Jsonb({
                    "a01_first_name": "Synthetic", "a01_middle_name": "",
                    "a01_last_name": "Head", "a02_relation_to_hh_head": "1"})))
        self.run = execute(
            "INSERT INTO assessment_runs (batch_id, calculation_version, calculation_provenance,"
            " expected_household_count, status, completed_at)"
            " VALUES (%s, 'synthetic-v1', %s, 1, 'completed', now()) RETURNING id",
            (self.batch, Jsonb({"kind": "synthetic"}))).fetchone()[0]
        findings = {
            "household_uuid": HOUSEHOLD, "survey_year": 2022,
            "mpi": {"uuid": HOUSEHOLD, "vulnerability_rank": 1},
            "programs": {name: {} for name in ("4Ps", "SLP", "SPISC", "TUPAD")},
        }
        execute("INSERT INTO household_assessments (run_id, batch_id, household_uuid, findings)"
                " VALUES (%s, %s, %s, %s)", (self.run, self.batch, HOUSEHOLD, Jsonb(findings)))
        execute("INSERT INTO assessment_run_selections (batch_id, run_id) VALUES (%s, %s)",
                (self.batch, self.run))
        self.staff_id, self.admin_id, self.inactive_id = [execute(
            "INSERT INTO staff_users (username, password_hash, role, is_active)"
            " VALUES (%s, 'scrypt:x$y$z', %s, %s) RETURNING id", (name, role, active),
        ).fetchone()[0] for name, role, active in (
            ("synthetic_staff", "lgu_staff", True), ("synthetic_admin", "system_administrator", True),
            ("inactive_staff", "lgu_staff", False))]

    def _record_send(self, address, token):
        """Pretend to email, after proving the account was already committed."""
        with self._connect() as other:
            visible = other.execute(
                "SELECT count(*) FROM citizen_account_tokens WHERE token_hash = %s",
                (hash_token("activation", token),)).fetchone()[0]
        self.sent.append({"address": address, "token": token, "committed_when_sent": visible == 1})

    def create(self, staff=None, household=HOUSEHOLD, form=None, run=None, batch=None):
        return service.create_citizen_account(
            staff or self.staff_id, household, batch or self.batch, run or self.run,
            form or valid_form(), connection_factory=self._connect,
        )

    def resend(self, staff=None, household=HOUSEHOLD):
        return service.resend_activation(
            staff or self.staff_id, household, self.batch, self.run,
            connection_factory=self._connect,
        )

    def counts(self):
        return tuple(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                     for table in ("citizen_accounts", "citizen_account_tokens",
                                   "citizen_account_events"))

    def error_code(self, function, *arguments, **keywords):
        with self.assertRaises(service.CitizenAccountError) as caught:
            function(*arguments, **keywords)
        return str(caught.exception)

    # --- creation ----------------------------------------------------------

    def test_creation_stores_a_pending_account_linked_to_the_household(self):
        result = self.create()
        account = repository.find_by_household(self.connection, self.batch, HOUSEHOLD)
        self.assertEqual((account["status"], account["representative_name"], account["email"]),
                         ("pending_activation", "Synthetic Citizen", "citizen@example.com"))
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertTrue(result["email_sent"])

    def test_creation_records_the_signed_in_staff_member_and_both_confirmations(self):
        self.create()
        account = repository.find_by_household(self.connection, self.batch, HOUSEHOLD)
        (event,) = repository.list_events(self.connection, account["id"])
        self.assertEqual((event["action"], event["staff_id"], event["note"]),
                         ("created", self.staff_id, NOTE))
        self.assertEqual((event["representative_verified"], event["privacy_consent_confirmed"]),
                         (True, True))

    def test_only_hashes_are_stored_and_the_code_matches_the_stored_hash(self):
        result = self.create()
        token, = self.sent
        stored = self.connection.execute(
            "SELECT token_hash, code_hash, purpose, expires_at > now() + interval '47 hours',"
            " expires_at < now() + interval '49 hours' FROM citizen_account_tokens").fetchone()
        self.assertEqual(stored[0], hash_token("activation", token["token"]))
        self.assertEqual((stored[2], stored[3], stored[4]), ("activation", True, True))
        self.assertTrue(activation_code_matches(stored[1], stored[0], result["activation_code"]))
        for value in (token["token"], result["activation_code"].replace(" ", "")):
            self.assertNotIn(value, " ".join(str(item) for item in stored))

    def test_the_email_is_sent_after_the_commit_and_then_recorded_as_accepted(self):
        self.create()
        (sent,) = self.sent
        self.assertEqual(sent["address"], "citizen@example.com")
        self.assertTrue(sent["committed_when_sent"])
        accepted = self.connection.execute(
            "SELECT email_accepted_at IS NOT NULL FROM citizen_account_tokens").fetchone()[0]
        self.assertTrue(accepted)

    def test_staff_see_the_code_but_never_the_link_or_token(self):
        result = self.create()
        token, = self.sent
        text = json.dumps(result)
        self.assertNotIn(token["token"], text)
        self.assertNotIn("/portal/", text)
        self.assertRegex(result["activation_code"], r"^\d{3} \d{3}$")
        self.assertEqual(set(result), {"email", "email_sent", "email_problem",
                                       "activation_code", "valid_hours"})
        self.assertEqual(result["valid_hours"], 48)

    def test_a_failed_email_keeps_the_pending_account_and_shows_no_code(self):
        self.send.side_effect = EmailDeliveryError("connection_failed")
        result = self.create()
        self.assertFalse(result["email_sent"])
        self.assertIsNone(result["activation_code"])
        self.assertEqual(result["email_problem"], "The email server could not be reached in time.")
        self.assertEqual(self.counts(), (1, 1, 1))
        self.assertIsNone(self.connection.execute(
            "SELECT email_accepted_at FROM citizen_account_tokens").fetchone()[0])

    def test_every_email_failure_gives_staff_a_fixed_message_and_a_way_to_resend(self):
        self.send.side_effect = EmailDeliveryError("not_configured")
        self.create()
        for code, message in service.ERROR_MESSAGES.items():
            with self.subTest(code=code):
                self.send.side_effect = EmailDeliveryError(code)
                result = self.resend()
                self.assertEqual((result["email_sent"], result["email_problem"]), (False, message))
                self.assertIsNone(result["activation_code"])

    def test_a_failure_while_recording_acceptance_does_not_hide_the_sent_email(self):
        with patch.object(service.citizen_accounts, "mark_email_accepted",
                          side_effect=psycopg.OperationalError("boom")):
            result = self.create()
        self.assertTrue(result["email_sent"])
        self.assertIsNotNone(result["activation_code"])

    # --- duplicates and rejected requests ----------------------------------

    def test_a_second_account_for_the_household_is_refused_without_changes(self):
        self.create()
        before = self.counts()
        self.send.reset_mock()
        code = self.error_code(self.create, form=valid_form(
            email="other@example.com", email_confirmation="other@example.com"))
        self.assertEqual(code, "household_has_account")
        self.assertEqual(self.counts(), before)
        self.send.assert_not_called()

    def test_an_email_used_elsewhere_is_refused_without_changes(self):
        self.connection.execute(
            "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
            " VALUES (%s, 'OTHER', 9, %s)", (self.batch, Jsonb({})))
        repository.insert_account(self.connection, self.batch, "OTHER", "Someone", "citizen@example.com")
        before = self.counts()
        self.assertEqual(self.error_code(self.create), "email_in_use")
        self.assertEqual(self.counts(), before)
        self.send.assert_not_called()

    def test_form_mistakes_never_touch_the_database_or_email(self):
        with self.assertRaises(service.CitizenAccountInputError):
            self.create(form=valid_form(verification_note="short"))
        self.assertEqual(self.counts(), (0, 0, 0))
        self.send.assert_not_called()

    def test_only_active_lgu_staff_may_create_accounts(self):
        for staff in (self.admin_id, self.inactive_id, self.staff_id + 999):
            with self.subTest(staff=staff):
                self.assertEqual(self.error_code(self.create, staff=staff), "unauthorized")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.send.assert_not_called()

    def test_the_selected_survey_and_household_must_still_match(self):
        self.assertEqual(self.error_code(self.create, run=self.run + 50), "context_changed")
        self.assertEqual(self.error_code(self.create, batch=self.batch + 50), "context_changed")
        self.assertEqual(self.error_code(self.create, household=NO_ASSESSMENT), "household_missing")
        self.assertEqual(self.error_code(self.create, household="UNKNOWN"), "household_missing")
        for bad in (("", self.batch, self.run), (HOUSEHOLD, 0, self.run),
                    (HOUSEHOLD, self.batch, "1"), ("x" * 129, self.batch, self.run)):
            with self.subTest(bad=bad):
                self.assertEqual(self.error_code(
                    service.create_citizen_account, self.staff_id, bad[0], bad[1], bad[2],
                    valid_form(), connection_factory=self._connect), "invalid_request")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.send.assert_not_called()

    def test_a_missing_hashing_key_creates_nothing(self):
        self.app.config["CITIZEN_CREDENTIAL_KEY"] = None
        self.assertEqual(self.error_code(self.create), "unavailable")
        self.assertEqual(self.counts(), (0, 0, 0))
        self.send.assert_not_called()

    def test_survey_data_and_reviews_are_unchanged(self):
        before = self.connection.execute(
            "SELECT batch_id, uuid, responses FROM households ORDER BY 2").fetchall()
        self.create()
        self.assertEqual(before, self.connection.execute(
            "SELECT batch_id, uuid, responses FROM households ORDER BY 2").fetchall())
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM staff_reviews").fetchone()[0], 0)

    # --- resend ------------------------------------------------------------

    def test_resend_replaces_the_old_link_and_code(self):
        self.create()
        old_token = self.sent[0]["token"]
        second = self.resend()
        new_token = self.sent[1]["token"]
        self.assertNotEqual(old_token, new_token)
        self.assertIsNone(repository.find_open_token(
            self.connection, hash_token("activation", old_token), "activation"))
        found = repository.find_open_token(
            self.connection, hash_token("activation", new_token), "activation")
        self.assertIsNotNone(found)
        self.assertTrue(activation_code_matches(
            found["code_hash"], hash_token("activation", new_token), second["activation_code"]))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FILTER (WHERE revoked_at IS NULL), count(*)"
            " FROM citizen_account_tokens").fetchone(), (1, 2))

    def test_resend_records_the_staff_member_and_emails_after_the_commit(self):
        self.create()
        self.resend()
        account = repository.find_by_household(self.connection, self.batch, HOUSEHOLD)
        events = repository.list_events(self.connection, account["id"])
        self.assertEqual([item["action"] for item in events], ["created", "activation_resent"])
        self.assertEqual(events[1]["staff_id"], self.staff_id)
        self.assertEqual(events[1]["note"], "Activation email resent.")
        self.assertTrue(self.sent[1]["committed_when_sent"])

    def test_a_failed_resend_still_cancels_the_old_pair(self):
        self.create()
        old_token = self.sent[0]["token"]
        self.send.side_effect = EmailDeliveryError("authentication_failed")
        result = self.resend()
        self.assertFalse(result["email_sent"])
        self.assertIsNone(result["activation_code"])
        self.assertIsNone(repository.find_open_token(
            self.connection, hash_token("activation", old_token), "activation"))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM citizen_account_tokens"
            " WHERE revoked_at IS NULL AND email_accepted_at IS NULL").fetchone()[0], 1)

    def test_resend_is_only_for_pending_accounts_that_exist(self):
        self.assertEqual(self.error_code(self.resend), "account_missing")
        self.create()
        account = repository.find_by_household(self.connection, self.batch, HOUSEHOLD)
        repository.activate(self.connection, account["id"], "scrypt:32768:8:1$salt$" + "d" * 32)
        self.assertEqual(self.error_code(self.resend), "not_pending")
        repository.deactivate(self.connection, account["id"])
        self.assertEqual(self.error_code(self.resend), "not_pending")
        self.assertEqual(self.error_code(self.resend, staff=self.admin_id), "unauthorized")

    # --- pages -------------------------------------------------------------

    def test_the_form_page_shows_the_household_and_its_grouped_contact_number(self):
        page = service.get_household_account_page(HOUSEHOLD, connection_factory=self._connect)
        self.assertEqual((page["head_name"], page["barangay"], page["contact_number"]),
                         ("Synthetic Head", "Synthetic Barangay", "0912 345 6789"))
        self.assertEqual((page["batch_id"], page["run_id"]), (self.batch, self.run))
        self.assertEqual(page["account"], {"state": "none"})
        self.create()
        page = service.get_household_account_page(HOUSEHOLD, connection_factory=self._connect)
        self.assertEqual(page["account"]["state"], "pending_activation")
        self.assertTrue(page["account"]["email_accepted_at"])
        self.assertTrue(page["account"]["link_valid"])

    def test_an_unassessed_household_has_no_account_page(self):
        for uuid in (NO_ASSESSMENT, "UNKNOWN"):
            with self.subTest(uuid=uuid):
                with self.assertRaises(AssessmentQueryError):
                    service.get_household_account_page(uuid, connection_factory=self._connect)

    def test_expired_and_cancelled_links_are_reported_as_not_valid(self):
        self.create()
        self.connection.execute(
            "UPDATE citizen_account_tokens SET created_at = now() - interval '3 days',"
            " expires_at = now() - interval '1 day'")
        page = service.get_household_account_page(HOUSEHOLD, connection_factory=self._connect)
        self.assertFalse(page["account"]["link_valid"])

    # --- Assessment page snapshot -------------------------------------------

    def detail(self):
        """Read the Assessment snapshot with the unrelated stored findings and sources stubbed."""
        evidence = assessment_queries.assessment_evidence
        with patch.object(assessment_queries.assessment_runs, "find_household_assessment",
                          return_value={"household_uuid": HOUSEHOLD}),                 patch.object(evidence, "find_household_sources", return_value={}),                 patch.object(evidence, "list_member_sources", return_value=[]),                 patch.object(evidence, "list_evidence_references", return_value=[]):
            return assessment_queries.get_active_assessment_detail(
                HOUSEHOLD, connection_factory=self._connect)

    def test_the_assessment_snapshot_includes_the_account_row(self):
        self.assertIsNone(self.detail()["citizen_account"])
        self.create()
        row = self.detail()["citizen_account"]
        self.assertEqual((row["status"], row["email"]), ("pending_activation", "citizen@example.com"))
        self.assertNotIn("password_hash", row)

    def test_missing_account_tables_never_break_the_assessment_snapshot(self):
        self.connection.execute(
            "DROP TABLE document_request_events, document_files, document_requests,"
            " citizen_account_events, citizen_account_tokens, citizen_accounts")
        detail = self.detail()
        self.assertEqual(detail["citizen_account"], PANEL_UNAVAILABLE)
        self.assertEqual(detail["identity"]["source_row_number"], 1)


if __name__ == "__main__":
    unittest.main()
