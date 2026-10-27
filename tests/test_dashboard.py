"""The municipal dashboard shows synthetic aggregates through staff-only routes."""

from datetime import datetime, timezone
from html import unescape
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from app import create_app
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import AssessmentQueryError


class DashboardRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "display_name": "Synthetic Staff Name",
                     "is_active": True, "session_version": 1}
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reviews = {
            "cards": [{"status": "in-review", "label": "In Review", "count": 2},
                      {"status": "follow-up", "label": "For LGU Follow-Up", "count": 1},
                      {"status": "no-action", "label": "No Further Action", "count": 0}],
            "recent": [{"id": 5, "household_uuid": "00000000-0000-0000-0000-000000000001",
                        "program": "SLP", "household_reference_id": "HH-0008",
                        "head_name": "Synthetic Head", "barangay": "Synthetic East",
                        "created_at": datetime(2026, 10, 17, 8, tzinfo=timezone.utc)}],
            "return_parameters": {"from_queue": "1", "queue_page": 1,
                                  "queue_status": "in-review", "queue_scope": "mine",
                                  "queue_sort": "desc"},
        }
        patcher = patch("routes.main.get_dashboard_reviews", return_value=self.reviews)
        self.reviews_query = patcher.start()
        self.addCleanup(patcher.stop)
        self.summary = {
            "barangay": "", "barangays": ["Synthetic East", "Synthetic West"],
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
        query.assert_called_once_with(barangay=None)
        for text in ("Dashboard", "Survey Year", "32", "Primary Priority",
                     "Secondary Priority", "Households with Undetermined Results", "Review Queue",
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

    def test_welcome_uses_display_name_and_falls_back_to_username(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            page = self.client.get("/").text
            self.assertIn("<h1>Dashboard</h1>", page)
            self.assertIn("Welcome back, Synthetic Staff Name!", page)
            self.assertEqual(page.count("<h1"), 1)
            self.user["display_name"] = None
            self.assertIn("Welcome back, synthetic_staff!", self.client.get("/").text)

    def test_review_cards_and_recent_reviews_link_to_queue_and_records(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            response = self.client.get("/")
        self.reviews_query.assert_called_once_with(1)
        for status in ("in-review", "follow-up", "no-action"):
            self.assertIn(f'href="/assessments/queue?scope=mine&amp;status={status}"', response.text)
        for text in ("My Subsidy Reviews", "In Review", "For LGU Follow-Up", "No Further Action",
                     "2 reviews", "1 review", "HH-0008", "Synthetic Head", "SLP",
                     "Started, not yet finished",
                     "Finished; further consideration recommended",
                     "Finished; no further action recommended"):
            self.assertIn(text, response.text)
        link = re.search(r'href="(/assessments/[^"]+/reviews/SLP/5[^"]*)"', response.text)
        self.assertIsNotNone(link)
        query = parse_qs(urlsplit(unescape(link.group(1))).query)
        self.assertEqual(query["from_queue"], ["1"])
        self.assertEqual(query["queue_scope"], ["mine"])
        self.assertEqual(query["queue_status"], ["in-review"])
        self.assertIn("Not changed by the barangay filter", response.text)
        self.assertEqual(response.text.count("View in Review Queue"), 3)
        cards = re.findall(r'<a class="dashboard-review-card.*?</a>', response.text, re.S)
        self.assertEqual(len(cards), 3)
        for card in cards:
            self.assertIn("View in Review Queue", card)
            self.assertNotIn("<button", card)
            self.assertEqual(card.count("<a "), 1)

    def test_barangay_filter_changes_statistics_only(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   return_value={**self.summary, "barangay": "Synthetic East"}) as query:
            response = self.client.get("/?barangay=Synthetic+East")
        query.assert_called_once_with(barangay="Synthetic East")
        self.reviews_query.assert_called_once_with(1)
        for text in ("Showing Barangay Synthetic East", "Show all barangays"):
            self.assertIn(text, response.text)
        self.assertIn('<option value="Synthetic East" selected>', response.text)
        workload = response.text.split('id="reviews-heading"')[1].split("</section>")[0]
        self.assertNotIn("barangay=", workload)
        self.assertNotRegex(response.text, r'<select[^>]*name="(year|survey_year)"')

    def test_filter_row_pairs_barangay_with_read_only_survey_year(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            text = self.client.get("/").text
        form = re.search(r'<form class="dashboard-filter".*?</form>', text, re.S)[0]
        self.assertLess(form.index('id="dashboard-barangay"'), form.index("Survey Year"))
        self.assertRegex(form, r'dashboard-filter-value">\s*2022\s*<')
        self.assertEqual(form.count("<select"), 1)
        self.assertEqual(form.count("<input"), 0)
        self.assertNotIn("Municipal overview", text)
        self.assertNotIn("Historical snapshot", text)
        self.assertNotIn("Needs Assessment Attention", text)
        self.assertIn("At least one assessment indicator could not be determined", text)
        self.assertIn('href="/assessments?completeness=unknown"', text)
        self.assertIn('href="/assessments/queue"', text)
        self.assertIn("Open the household list or the Review Queue.", text)

    def test_invalid_barangay_is_rejected(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=InvalidAssessmentFilters("invalid_dashboard_barangay")):
            self.assertEqual(self.client.get("/?barangay=Nowhere").status_code, 400)
        self.assertEqual(self.client.get("/?barangay=A&barangay=B").status_code, 400)

    def test_empty_and_unavailable_reviews_leave_statistics_visible(self):
        self._sign_in()
        self.reviews_query.return_value = {**self.reviews, "recent": [],
                                           "cards": [{**c, "count": 0} for c in self.reviews["cards"]]}
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            self.assertIn("You have no reviews in progress.", self.client.get("/").text)
            self.reviews_query.side_effect = AssessmentQueryError("private_database_details")
            response = self.client.get("/")
        self.assertIn("Your reviews are unavailable", response.text)
        self.assertIn("Household snapshot", response.text)
        self.assertNotIn("private_database_details", response.text)
        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            response = self.client.get("/")
        self.assertIn("Your reviews are unavailable", response.text)
        self.assertIn("Assessment summary unavailable", response.text)

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

    def test_admin_dashboard_shares_summary_and_assessment_links(self):
        self.user["role"] = "system_administrator"
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary) as query:
            response = self.client.get("/administration")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with(barangay=None)
        for text in ("Dashboard", "Survey Year", "Primary Priority",
                     "Secondary Priority", "Household snapshot", "Main water source"):
            self.assertIn(text, response.text)
        self.assertIn('href="/administration" aria-current="page"', response.text)
        self.assertIn('href="/households"', response.text)
        self.assertIn('href="/assessments?priority=primary"', response.text)
        self.assertIn('href="/assessments/queue"', response.text)
        self.assertEqual(self.client.get("/").location, "/administration")
        self.reviews_query.assert_not_called()
        self.assertNotIn("My Subsidy Reviews", response.text)
        self.assertNotIn("Welcome back", response.text)
        self.assertIn("<h1>Dashboard</h1>", response.text)

        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            unavailable = self.client.get("/administration")
        self.assertEqual(unavailable.status_code, 200)
        self.assertIn("Assessment summary unavailable", unavailable.text)
        self.assertNotIn("private_database_details", unavailable.text)


if __name__ == "__main__":
    unittest.main()
