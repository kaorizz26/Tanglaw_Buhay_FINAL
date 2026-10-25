"""Household route checks use synthetic records and no source database."""

from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app import create_app
from services.assessment_queries import AssessmentQueryError
from services.household_records import InvalidRecordFilters, RecordFilters, parse_record_arguments
from werkzeug.datastructures import MultiDict


HOUSEHOLD_UUID = UUID("11111111-1111-4111-8111-111111111111")


class HouseholdRecordRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = {"survey_year": 2022, "municipality": "Pozorrubio"}
        self.directory = {
            "context": self.context, "filters": RecordFilters(),
            "barangays": ["Synthetic East", "Synthetic West"],
            "total": 1, "page": 1, "page_count": 1,
            "range_start": 1, "range_end": 1,
            "items": [{"household_uuid": HOUSEHOLD_UUID, "reference_id": "HH-00001",
                       "head_name": "Synthetic Head", "barangay": "Synthetic East",
                       "member_count": 2}],
        }
        self.record = {
            "context": self.context, "household_uuid": str(HOUSEHOLD_UUID),
            "reference_id": "HH-00001", "head_name": "Synthetic Head",
            "barangay": "Synthetic East", "member_count": 2,
            "groups": [{"title": "Water and sanitation", "cells": [
                {"label": "Main water source", "value": "Synthetic answer"}]}],
            "members": [{"line_number": 1, "name": "Synthetic Head",
                         "cells": [{"label": "Age at survey", "value": "35 years old"}]}],
        }

    def _sign_in(self, role="lgu_staff"):
        self.user["role"] = role
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token=token_urlsafe(32))

    def test_directory_requires_login_and_both_roles_can_read_it(self):
        self.assertEqual(self.client.get("/households").location, "/login")
        self.assertEqual(self.client.get(f"/households/{HOUSEHOLD_UUID}").location, "/login")
        for role in ("lgu_staff", "system_administrator"):
            self._sign_in(role)
            with patch("routes.households.get_record_page", return_value=self.directory) as query:
                response = self.client.get("/households")
            self.assertEqual(response.status_code, 200)
            query.assert_called_once_with(1, RecordFilters())
            self.assertIn("Household Records", response.text)
            self.assertIn("Synthetic Head", response.text)
            self.assertIn("Synthetic East", response.text)
            self.assertIn('href="/households" aria-current="page"', response.text)
            self.assertIn("All barangays", response.text)
            self.assertIn('Cache-Control', response.headers)
            self.assertNotIn(str(HOUSEHOLD_UUID), response.text.split("<main", 1)[0])

    def test_filters_and_return_link_are_preserved(self):
        self._sign_in()
        filtered = {**self.directory, "filters": RecordFilters("Synthetic", "Synthetic East"),
                    "page": 2, "page_count": 3, "total": 60,
                    "range_start": 26, "range_end": 50,
                    "items": self.directory["items"] * 25}
        with patch("routes.households.get_record_page", return_value=filtered) as query:
            response = self.client.get("/households?q=Synthetic&barangay=Synthetic+East&page=2")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with(2, RecordFilters("Synthetic", "Synthetic East"))
        self.assertIn('value="Synthetic"', response.text)
        self.assertIn('value="Synthetic East" selected', response.text)
        self.assertIn('page=3', response.text)
        self.assertIn('barangay=Synthetic+East', response.text)
        self.assertIn("Showing <strong>26–50</strong> of 60 households", response.text)
        with patch("routes.households.get_record_detail", return_value=self.record):
            detail = self.client.get(
                f"/households/{HOUSEHOLD_UUID}?q=Synthetic&barangay=Synthetic+East&page=2"
            )
        self.assertEqual(detail.status_code, 200)
        self.assertIn('/households?page=2&amp;q=Synthetic&amp;barangay=Synthetic+East', detail.text)
        self.assertIn(f'/assessments/{HOUSEHOLD_UUID}', detail.text)
        self.assertIn("Synthetic answer", detail.text)
        self.assertIn("35 years old", detail.text)
        self.assertIn("historical", detail.text.lower())
        self.assertIn('href="#household-answers"', detail.text)
        self.assertIn('id="household-answers"', detail.text)
        self.assertIn('href="#member-roster"', detail.text)
        self.assertIn('id="member-roster"', detail.text)
        self._sign_in("system_administrator")
        with patch("routes.households.get_record_detail", return_value=self.record):
            admin_detail = self.client.get(f"/households/{HOUSEHOLD_UUID}")
        self.assertEqual(admin_detail.status_code, 200)
        self.assertIn(f'/assessments/{HOUSEHOLD_UUID}', admin_detail.text)

    def test_invalid_filters_missing_record_and_unavailable_database_fail_safely(self):
        self._sign_in()
        with patch("routes.households.get_record_page", return_value=self.directory) as query:
            self.assertEqual(self.client.get("/households?page=0").status_code, 400)
            self.assertEqual(self.client.get("/households?q=a&q=b").status_code, 400)
            query.assert_not_called()
        with patch("routes.households.get_record_page", return_value=None):
            self.assertEqual(self.client.get("/households?page=9").status_code, 404)
        with patch("routes.households.get_record_page",
                   side_effect=AssessmentQueryError("private_source_detail")):
            response = self.client.get("/households")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private_source_detail", response.text)
        with patch("routes.households.get_record_detail",
                   side_effect=AssessmentQueryError("household_record_missing")):
            self.assertEqual(self.client.get(f"/households/{HOUSEHOLD_UUID}").status_code, 404)
        self.assertEqual(self.client.get("/households/OLD_PRIVATE_ID").status_code, 410)

    def test_synthetic_source_text_is_escaped(self):
        self._sign_in()
        bad = {**self.directory, "items": [{**self.directory["items"][0],
                                            "head_name": "<script>alert(1)</script>"}]}
        with patch("routes.households.get_record_page", return_value=bad):
            response = self.client.get("/households")
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)


class HouseholdRecordFilterTests(unittest.TestCase):
    def test_search_and_barangay_are_normalized_and_duplicates_rejected(self):
        page, filters = parse_record_arguments(MultiDict([
            ("q", "  Synthetic   Head  "), ("barangay", " Synthetic East "), ("page", "2")
        ]))
        self.assertEqual((page, filters), (2, RecordFilters("Synthetic Head", "Synthetic East")))
        for arguments in (MultiDict([("q", "a"), ("q", "b")]),
                          MultiDict([("page", "-1")]),
                          MultiDict([("barangay", "x" * 201)])):
            with self.assertRaises(InvalidRecordFilters):
                parse_record_arguments(arguments)


if __name__ == "__main__":
    unittest.main()
