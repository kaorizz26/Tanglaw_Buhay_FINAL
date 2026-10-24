"""The municipal dashboard shows synthetic aggregates through staff-only routes."""

from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from services.assessment_queries import AssessmentQueryError


class DashboardRouteTests(unittest.TestCase):
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
        self.summary = {
            "context": {"survey_year": 2022, "municipality": "Pozorrubio",
                        "household_count": 32},
            "priority": {"all": 32, "primary": 4, "secondary": 7,
                         "both": 11, "other": 21},
            "findings": {"assessed_households": 32, "needs_attention": 3,
                         "four_ps": 8, "slp": 6, "spisc": 2, "tupad": 1},
            "household_profile": {
                "average_members": 3.5, "with_children": 18,
                "with_older_adults": 9, "with_unclear_age": 0,
                "without_electricity": 2, "electricity_unclear": 0,
                "shared_toilet": 4, "toilet_sharing_unclear": 0,
                "water_sources": [
                    {"label": "Piped into dwelling", "count": 24,
                     "percent": 75.0, "color": "#244b65"},
                    {"label": "Protected well", "count": 8,
                     "percent": 25.0, "color": "#b78d42"},
                ],
                "water_gradient": "conic-gradient(#244b65 0% 75%, #b78d42 75% 100%)",
            },
        }

    def _sign_in(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token=token_urlsafe(32))

    def test_staff_dashboard_shows_only_aggregates_and_working_destinations(self):
        self.assertEqual(self.client.get("/").location, "/login")
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary) as query:
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with()
        for text in ("Dashboard", "CBMS 2022", "32", "Primary Priority",
                     "Secondary Priority", "Needs Assessment Attention", "Review Queue",
                     "Household snapshot", "3.50", "Households with someone under 18",
                     "Main water source", "Piped into dwelling", "recorded no electricity"):
            self.assertIn(text, response.text)
        self.assertIn('href="/" aria-current="page"', response.text)
        self.assertIn('href="/assessments?priority=primary"', response.text)
        self.assertIn('href="/assessments?completeness=unknown"', response.text)
        self.assertIn('href="/assessments?program=4Ps"', response.text)
        self.assertIn('href="/assessments/queue"', response.text)
        self.assertNotIn("household_uuid", response.text)
        self.assertNotIn("Synthetic Household", response.text)
        self.assertNotIn("Single parent", response.text)

    def test_missing_assessments_keep_staff_home_and_fixture_boundary(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Assessment summary unavailable", response.text)
        self.assertNotIn("private_database_details", response.text)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/").status_code, 403)

    def test_admin_dashboard_shares_summary_without_assessment_links(self):
        self.user["role"] = "system_administrator"
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary) as query:
            response = self.client.get("/administration")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with()
        for text in ("Dashboard", "CBMS 2022", "Primary Priority",
                     "Secondary Priority", "Household snapshot", "Main water source"):
            self.assertIn(text, response.text)
        self.assertIn('href="/administration" aria-current="page"', response.text)
        self.assertIn('href="/households"', response.text)
        self.assertNotIn('href="/assessments', response.text)
        self.assertNotIn("Staff assessment pages", response.text)
        self.assertEqual(self.client.get("/").location, "/administration")

        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            unavailable = self.client.get("/administration")
        self.assertEqual(unavailable.status_code, 200)
        self.assertIn("Assessment summary unavailable", unavailable.text)
        self.assertNotIn('href="/assessments', unavailable.text)
        self.assertNotIn("private_database_details", unavailable.text)


if __name__ == "__main__":
    unittest.main()
