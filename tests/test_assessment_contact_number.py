"""Contact number on the Household Assessment page, using synthetic data only."""

import os
from pathlib import Path
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from repositories import assessment_evidence
from services import assessment_details as details
from services import staff_reviews as review_service

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")
RAW_NUMBER = "09123456789"
SHOWN_NUMBER = "0912 345 6789"


class ContactNumberFormatTests(unittest.TestCase):
    def test_valid_numbers_are_grouped_and_keep_the_leading_zero(self):
        self.assertEqual(details.format_contact_number(RAW_NUMBER), SHOWN_NUMBER)
        self.assertEqual(details.format_contact_number("09000000001"), "0900 000 0001")
        self.assertEqual(details.format_contact_number(f" {RAW_NUMBER}\n"), SHOWN_NUMBER)

    def test_missing_or_invalid_values_are_not_recorded(self):
        for value in (None, "", "   ", "NA", "9123456789", "0912345678", "091234567890",
                      "+639123456789", "0912-345-6789", "0912 345 6789", "08123456789",
                      "09abcdefghi", 9123456789, 91234567890):
            with self.subTest(value=value):
                self.assertEqual(details.format_contact_number(value), "Not recorded")

    def test_prepared_detail_uses_the_formatted_value_and_tolerates_a_missing_key(self):
        source = detail_source()
        self.assertEqual(details.prepare_assessment_detail(source)["contact_number"],
                         "Not recorded")
        source["contact_number"] = RAW_NUMBER
        self.assertEqual(details.prepare_assessment_detail(source)["contact_number"],
                         SHOWN_NUMBER)


class ContactNumberPageTests(unittest.TestCase):
    def setUp(self):
        self.source = detail_source()
        self.app = create_app(
            {"TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None},
        )
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        patches = (
            patch("psycopg.connect", side_effect=AssertionError("No real database connections")),
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
            patch.object(details, "get_active_assessment_detail", return_value=self.source),
            # The Staff Reviews tab loads the same stored assessment through its own import.
            patch.object(review_service, "get_active_assessment_detail",
                         return_value=self.source),
        )
        mocks = []
        for patcher in patches:
            mocks.append(patcher.start())
            self.addCleanup(patcher.stop)
        self.queries = mocks[-2:]
        self.path = f"/assessments/{HOUSEHOLD_UUID}"

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token="synthetic-csrf")

    def test_both_staff_roles_see_the_grouped_number_in_the_household_details(self):
        self.source["contact_number"] = RAW_NUMBER
        for role in ("lgu_staff", "system_administrator"):
            with self.subTest(role=role), patch.dict(self.user, role=role):
                self._sign_in()
                for query in ("", "?tab=mpi", "?tab=reviews"):
                    page = self.client.get(self.path + query).get_data(as_text=True)
                    self.assertIn("Contact number", page)
                    self.assertIn(SHOWN_NUMBER, page)
                    self.assertNotIn(RAW_NUMBER, page)

    def test_missing_and_invalid_values_show_not_recorded_without_the_raw_value(self):
        self._sign_in()
        for value in (None, "", "12345", "not-a-number"):
            with self.subTest(value=value):
                self.source["contact_number"] = value
                page = self.client.get(self.path).get_data(as_text=True)
                self.assertRegex(
                    page, r'household-contact-number">\s*Not recorded\s*<',
                )
                if value:
                    self.assertNotIn(value, page)

    def test_unauthenticated_requests_never_reach_the_number(self):
        self.source["contact_number"] = RAW_NUMBER
        response = self.client.get(self.path)
        self.assertEqual(response.location, "/login")
        self.assertNotIn(SHOWN_NUMBER, response.get_data(as_text=True))
        for query in self.queries:
            query.assert_not_called()
        # A revoked or inactive account is refused in the same way.
        for field, value in (("is_active", False), ("session_version", 2)):
            with self.subTest(field=field), patch.dict(self.user, {field: value}):
                self._sign_in()
                self.assertEqual(self.client.get(self.path).location, "/login")
        for query in self.queries:
            query.assert_not_called()

    def test_the_number_is_not_placed_in_any_link_or_form_address(self):
        self.source["contact_number"] = RAW_NUMBER
        self._sign_in()
        page = self.client.get(self.path + "?tab=reviews").get_data(as_text=True)
        for attribute in ("href", "action", "src"):
            for address in re.findall(rf'{attribute}="([^"]*)"', page):
                self.assertNotIn(RAW_NUMBER, address)
                self.assertNotIn(SHOWN_NUMBER, address)

    def test_only_the_single_household_detail_service_reads_the_number(self):
        """Lists and filters must never load contact numbers."""
        readers = []
        for folder in ("routes", "services", "repositories"):
            for path in (ROOT / folder).glob("*.py"):
                if "find_household_contact_number" in path.read_text(encoding="utf-8"):
                    readers.append(f"{folder}/{path.name}")
        # The Assessment page and the Create Citizen Account page each show one household.
        self.assertEqual(sorted(readers), [
            "repositories/assessment_evidence.py", "services/assessment_queries.py",
            "services/citizen_accounts.py",
        ])


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for synthetic contact tests.")
class ContactNumberQueryTests(unittest.TestCase):
    def setUp(self):
        settings = conninfo_to_dict(TEST_URL)
        self.assertEqual(settings.get("dbname"), "tanglaw_buhay_test")
        self.schema = f"contact_query_{uuid4().hex}"
        self.connection = psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True)
        self.addCleanup(self._drop_schema)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(
            sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)),
        )
        self.connection.execute((ROOT / "migrations/0001_database_foundation.sql").read_text("utf-8"))
        for _ in range(2):
            self.connection.execute(
                "INSERT INTO dataset_batches (survey_year, municipality, source_columns)"
                " VALUES (2022, 'Pozorrubio', %s)", (Jsonb({}),),
            )
        self.first, self.second = [row[0] for row in self.connection.execute(
            "SELECT id FROM dataset_batches ORDER BY id").fetchall()]
        for batch, uuid, answers in (
            (self.first, "H-1", {"contact_number": RAW_NUMBER}),
            (self.first, "H-2", {"barangay": "Synthetic"}),
            (self.second, "H-1", {"contact_number": "09000000001"}),
        ):
            self.connection.execute(
                "INSERT INTO households (batch_id, uuid, source_row_number, responses)"
                " VALUES (%s, %s, (SELECT count(*) + 1 FROM households WHERE batch_id = %s), %s)",
                (batch, uuid, batch, Jsonb(answers)),
            )

    def _drop_schema(self):
        self.connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)),
        )
        self.connection.close()

    def _find(self, batch, uuid):
        return assessment_evidence.find_household_contact_number(self.connection, batch, uuid)

    def test_lookup_is_scoped_to_the_batch_and_household(self):
        self.assertEqual(self._find(self.first, "H-1"), RAW_NUMBER)
        self.assertEqual(self._find(self.second, "H-1"), "09000000001")

    def test_missing_key_or_household_returns_none(self):
        self.assertIsNone(self._find(self.first, "H-2"))
        self.assertIsNone(self._find(self.second, "H-2"))
        self.assertIsNone(self._find(self.first, "UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
