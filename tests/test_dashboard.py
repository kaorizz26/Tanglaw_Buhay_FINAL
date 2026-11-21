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
from services.staff_access import PERMISSION_CODES
from services.assessment_filters import InvalidAssessmentFilters
from services.assessment_queries import AssessmentQueryError
from services.dashboard_patterns import present_indicator_patterns
from services.vulnerability.scoring import INDICATOR_WEIGHTS


class DashboardRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "display_name": "Synthetic Staff Name",
                     "is_active": True, "session_version": 1,
                     "permissions": PERMISSION_CODES}
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
            "indicator_patterns": present_indicator_patterns({
                "assessed_households": 32,
                "indicators": {name: {"deprived": 4, "not_deprived": 24, "unknown": 4}
                               for name in INDICATOR_WEIGHTS}}, 32),
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
                     "Household Snapshot", "3.50", "Households with someone under 18",
                     "Main water source", "Piped into dwelling", "recorded no electricity"):
            self.assertIn(text, response.text)
        self.assertIn('href="/" aria-current="page"', response.text)
        self.assertIn('href="/assessments?priority=primary"', response.text)
        self.assertIn('href="/assessments?completeness=unknown"', response.text)
        self.assertIn('href="/assessments?program=4Ps"', response.text)
        self.assertIn('href="/assessments/queue"', response.text)
        self.assertIn('<details class="navigation-group" open>', response.text)
        section_nav = response.text.split('class="dashboard-section-nav"', 1)[1].split(
            "</nav>", 1
        )[0]
        for target in ("#reviews-heading", "#statistics-heading", "#household-heading"):
            self.assertIn(f'href="{target}"', section_nav)
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
        self.assertEqual(form.count("<input"), 1)
        self.assertIn('type="hidden" id="dashboard-area" name="area" value="education"', form)
        self.assertNotIn("Municipal overview", text)
        self.assertNotIn("Historical snapshot", text)
        self.assertNotIn("Needs Assessment Attention", text)
        self.assertIn("Some survey information was missing or unclear", text)
        self.assertIn('href="/assessments?completeness=unknown"', text)
        self.assertIn('href="/assessments/queue"', text)
        self.assertIn("Open the household list or the Review Queue.", text)
        self.assertNotIn("Priority groups", text)

    def test_assessment_patterns_show_four_areas_with_education_selected(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            text = self.client.get("/").text
        self.assertIn("Household Assessment Patterns", text)
        self.assertIn("Explore the household conditions identified in the 2022 assessment.", text)
        buttons = re.findall(r'<button type="button" id="pattern-area-(\w+)".*?aria-pressed="(\w+)"',
                             text, re.S)
        self.assertEqual(buttons, [("education", "true"), ("health", "false"),
                                   ("housing", "false"), ("employment", "false")])
        self.assertEqual(text.count("aria-pressed"), 4)
        self.assertNotIn("aria-selected", text)
        panels = re.findall(r'<div id="pattern-panel-(\w+)"[^>]*?(hidden)?>', text)
        self.assertEqual([p for p, hidden in panels if not hidden], ["education"])
        self.assertEqual(text.count('class="pattern-row"'), 16)
        for phrase in ("Condition flagged", "Condition not flagged", "Missing or unclear answers",
                       "Could Not Determine", "Illness and Treatment", "Wanting More Work Hours"):
            self.assertIn(phrase, text)
        self.assertIn("Based on 32 assessed households in all barangays.", text)
        self.assertIn("4 <span>(12.5%)</span>", text)
        section = text.split('id="patterns-heading"')[1].split("</section>")[0]
        self.assertNotIn("<a ", section)
        self.assertNotIn("View Household Prioritization", text)

    def test_selected_area_is_validated_and_carried_by_the_filter(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   return_value={**self.summary, "barangay": "Synthetic East"}):
            text = self.client.get("/?barangay=Synthetic+East&area=employment").text
            fallback = self.client.get("/?area=%3Cscript%3E").text
        self.assertRegex(text, r'id="pattern-area-employment"[^>]*aria-pressed="true"')
        self.assertIn('name="area" value="employment"', text)
        self.assertIn('href="/?area=employment" data-keep-area', text)
        self.assertIn('name="area" value="education"', fallback)
        self.assertNotIn("<script>", fallback)

    def test_historical_links_keep_the_selected_barangay_only(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   return_value={**self.summary, "barangay": "Synthetic East"}):
            text = self.client.get("/?barangay=Synthetic+East").text
        historical = text.split('id="statistics-heading"')[0].split("</header>")[0] +             text.split('id="statistics-heading"')[1]
        links = re.findall(r'href="(/assessments\?[^"]*)"', historical)
        self.assertGreaterEqual(len(links), 8)
        for link in links:
            self.assertEqual(parse_qs(urlsplit(unescape(link)).query)["barangay"], ["Synthetic East"], link)
        header = re.search(r'<a class="dashboard-primary-link" href="([^"]+)"', text)[1]
        self.assertEqual(unescape(header), "/assessments?barangay=Synthetic+East")
        workload = text.split('id="reviews-heading"')[1].split("</section>")[0]
        self.assertNotIn("barangay=", workload)

    def test_bottom_assessment_pages_follow_snapshot_and_keep_review_queue_independent(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary",
                   return_value={**self.summary, "barangay": "Synthetic East"}):
            text = self.client.get("/?barangay=Synthetic+East").text
        self.assertLess(text.index('id="household-heading"'), text.index('id="work-heading"'))
        work = text.split('id="work-heading"')[1].split("</section>")[0]
        self.assertIn('href="/assessments?barangay=Synthetic+East">Household Prioritization', work)
        self.assertIn('href="/assessments/queue">Review Queue', work)
        self.assertEqual(text.count('id="work-heading"'), 1)
        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            text = self.client.get("/?barangay=Synthetic+East").text
        work = text.split('id="work-heading"')[1].split("</section>")[0]
        self.assertIn('href="/assessments">Household Prioritization', work)
        self.assertIn('href="/assessments/queue">Review Queue', work)

    def test_kpi_captions_screening_text_and_snapshot_wording(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            text = self.client.get("/").text
        cards = re.findall(r'<(?:div|a) class="dashboard-metric[ "].*?</(?:div|a)>',
                           text.split('aria-label="Assessment summary"')[1], re.S)
        self.assertEqual(len(cards), 4)
        for label, caption in (
            ("Assessed Households", "Households evaluated by the system"),
            ("Primary Priority", "Higher vulnerability score with a program flagged for review"),
            ("Secondary Priority", "Moderate vulnerability score with a program flagged for review"),
            ("Households with Undetermined Results", "Some survey information was missing or unclear"),
        ):
            card = next(c for c in cards if label in c)
            self.assertIn(caption, card)
        screening = text.split('id="screening-heading"')[1].split("</section>")[0]
        for phrase in ("System findings", "more than one program",
                       "not an approval or a staff review", "Explore screening findings"):
            self.assertIn(phrase, screening)
        self.assertNotIn("immediate", screening)
        snapshot = text.split('id="household-heading"')[1].split("</section>")[0]
        for phrase in ("Household members", "Housing &amp; utilities", "Main water source",
                       "do not show subsidy eligibility", "Average recorded members per household",
                       "recorded no electricity", "recorded a shared toilet"):
            self.assertIn(phrase, snapshot)
        for banned in ("Roof", "Walls", "Floor"):
            self.assertNotIn(banned, snapshot)

    def test_snapshot_flags_unclear_answers_without_counting_them_as_negative(self):
        self._sign_in()
        profile = {**self.summary["household_profile"], "electricity_unclear": 3,
                   "toilet_sharing_unclear": 2, "with_unclear_age": 1}
        with patch("routes.main.get_active_dashboard_summary",
                   return_value={**self.summary, "household_profile": profile}):
            text = self.client.get("/").text
        for phrase in ("3 households had no clear Yes or No electricity response.",
                       "2 households had no Yes or No toilet-sharing response.",
                       "whose age was not recorded clearly"):
            self.assertIn(phrase, text)

    def test_header_link_works_without_a_summary_or_barangay(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary):
            text = self.client.get("/").text
        self.assertIn('class="dashboard-primary-link" href="/assessments"', text)
        self.assertNotIn("barangay=", text.split("Historical household statistics")[1])
        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")):
            text = self.client.get("/").text
        self.assertIn('class="dashboard-primary-link" href="/assessments"', text)
        self.assertNotIn("Household Assessment Patterns", text)

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
        self.assertIn("Household Snapshot", response.text)
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
        account_summary = {
            "total": 8, "active": 6, "inactive": 2,
            "pending_setup": 2, "setup_complete": 4,
        }
        review_summary = {
            "statuses": [
                {"key": "in-review", "label": "In Review", "count": 5},
                {"key": "follow-up", "label": "For LGU Follow-Up", "count": 3},
                {"key": "no-action", "label": "No Further Action", "count": 2},
            ],
            "programs": [
                {"code": "4Ps", "count": 2},
                {"code": "SLP", "count": 1},
                {"code": "SPISC", "count": 1},
                {"code": "TUPAD", "count": 1},
            ],
            "total": 10,
            "active_total": 5,
        }
        import_summary = {
            "active": {"id": 4, "survey_year": 2022, "households": 32, "run_id": 9},
            "statuses": [
                {"key": "active", "label": "Active", "count": 1},
                {"key": "ready", "label": "Ready to activate", "count": 1},
                {"key": "awaiting", "label": "Awaiting preparation", "count": 1},
            ],
            "years": [
                {"year": 2024, "count": 2},
                {"year": 2022, "count": 1},
            ],
            "total": 3,
        }
        barangay_summary = {
            "context": {"survey_year": 2022},
            "items": [
                {"barangay": "Synthetic East", "households": 20,
                 "primary": 5, "secondary": 3, "priority_count": 8,
                 "primary_percent": 25.0, "secondary_percent": 15.0,
                 "priority_percent": 40.0},
                {"barangay": "Synthetic West", "households": 10,
                 "primary": 1, "secondary": 2, "priority_count": 3,
                 "primary_percent": 10.0, "secondary_percent": 20.0,
                 "priority_percent": 30.0},
            ],
            "recorded_barangays": 2, "shown_barangays": 2,
            "unrecorded_households": 2,
        }
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary) as query, \
                patch("routes.main.get_staff_account_dashboard_summary",
                      return_value=account_summary), \
                patch("routes.main.get_admin_review_dashboard_summary",
                      return_value=review_summary), \
                patch("routes.main.get_import_dashboard_summary",
                      return_value=import_summary), \
                patch("routes.main.get_active_barangay_priority_summary",
                      return_value=barangay_summary):
            response = self.client.get("/administration")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with(barangay=None)
        for text in ("Dashboard", "Survey Year", "Primary Priority",
                     "Secondary Priority", "Household Snapshot", "Main water source",
                     "Household Assessment Patterns", "Findings marked",
                     "Administrative Overview", "Assessed households by priority group",
                     "Assessment result coverage", "Other assessed", "Complete results",
                     "Staff Account Overview", "Active and inactive accounts",
                     "Password setup progress", "Pending setup",
                     "Review Workload Overview", "Current review queue",
                     "In Review by program", "Dataset and Import Overview",
                     "Stored batch status", "Stored batches by survey year",
                     "Barangay Priority Comparison", "Priority-group share by barangay"):
            self.assertIn(text, response.text)
        ordered_admin_sections = [
            "Dataset and Import Overview",
            "Historical household statistics",
            "Administrative Overview",
            "Barangay Priority Comparison",
            "Household Assessment Patterns",
            "Household Snapshot",
            "Review Workload Overview",
            "Staff Account Overview",
        ]
        dashboard_body = response.text.split('aria-label="Dashboard sections"', 1)[1].split(
            "</nav>", 1
        )[1]
        section_positions = [dashboard_body.index(text) for text in ordered_admin_sections]
        self.assertEqual(section_positions, sorted(section_positions))
        self.assertIn('href="/administration" aria-current="page"', response.text)
        self.assertIn('href="/households"', response.text)
        self.assertIn('href="/assessments?priority=primary"', response.text)
        self.assertIn('href="/assessments/queue"', response.text)
        self.assertEqual(self.client.get("/").location, "/administration")
        self.reviews_query.assert_not_called()
        self.assertNotIn("My Subsidy Reviews", response.text)
        self.assertNotIn("Welcome back", response.text)
        self.assertIn("<h1>Dashboard</h1>", response.text)
        self.assertIn('href="#admin-overview-heading"', response.text)
        self.assertIn('href="#staff-account-overview-heading"', response.text)
        self.assertIn('href="#review-workload-heading"', response.text)
        self.assertIn('href="#dataset-status-heading"', response.text)
        self.assertIn('href="#barangay-priority-heading"', response.text)
        self.assertIn("Active accounts: 6, 75.0 percent", response.text)
        self.assertIn("Inactive accounts: 2, 25.0 percent", response.text)
        self.assertIn("Password setup complete: 4, 66.7 percent", response.text)
        self.assertIn("Pending password setup: 2, 33.3 percent", response.text)
        self.assertIn("In Review: 5 reviews", response.text)
        self.assertIn("For LGU Follow-Up: 3 reviews", response.text)
        self.assertIn("No Further Action: 2 reviews", response.text)
        self.assertIn('href="/assessments/queue?scope=all&amp;status=in-review&amp;program=4Ps"',
                      response.text)
        self.assertIn("Active: 1 batch", response.text)
        self.assertIn("Ready to activate: 1 batch", response.text)
        self.assertIn("Awaiting preparation: 1 batch", response.text)
        self.assertIn("Batch 4 · 32 households", response.text)
        self.assertIn("Barangay Synthetic East: Primary Priority 5 households, 25.0 percent",
                      response.text)
        self.assertIn("combined 8 of 20 households, 40.0 percent", response.text)
        self.assertIn('href="/assessments?barangay=Synthetic+East"', response.text)
        self.assertIn("2 assessed households had no clear recorded barangay", response.text)
        self.assertIn("Primary Priority: 4 households, 12.5 percent", response.text)
        self.assertIn("Secondary Priority: 7 households, 21.9 percent", response.text)
        self.assertIn("Other assessed households: 21, 65.6 percent", response.text)
        self.assertIn("Complete results: 29 households, 90.6 percent", response.text)
        self.assertIn("Results needing attention: 3 households, 9.4 percent", response.text)

        with patch("routes.main.get_active_dashboard_summary",
                   side_effect=AssessmentQueryError("private_database_details")), \
                patch("routes.main.get_staff_account_dashboard_summary",
                      return_value=account_summary), \
                patch("routes.main.get_admin_review_dashboard_summary",
                      return_value=review_summary), \
                patch("routes.main.get_import_dashboard_summary",
                      return_value=import_summary), \
                patch("routes.main.get_active_barangay_priority_summary",
                      return_value=barangay_summary):
            unavailable = self.client.get("/administration")
        self.assertEqual(unavailable.status_code, 200)
        self.assertIn("Assessment summary unavailable", unavailable.text)
        self.assertIn("Staff Account Overview", unavailable.text)
        self.assertIn("Review Workload Overview", unavailable.text)
        self.assertIn("Dataset and Import Overview", unavailable.text)
        self.assertIn("Barangay Priority Comparison", unavailable.text)
        self.assertNotIn("private_database_details", unavailable.text)

    def test_staff_dashboard_does_not_show_administrator_overview(self):
        self._sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=self.summary), \
                patch("routes.main.get_staff_account_dashboard_summary") as account_query, \
                patch("routes.main.get_admin_review_dashboard_summary") as review_query, \
                patch("routes.main.get_import_dashboard_summary") as import_query, \
                patch("routes.main.get_active_barangay_priority_summary") as barangay_query:
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Administrative Overview", response.text)
        self.assertNotIn('href="#admin-overview-heading"', response.text)
        self.assertNotIn("Assessed households by priority group", response.text)
        self.assertNotIn("Staff Account Overview", response.text)
        self.assertNotIn("Dataset and Import Overview", response.text)
        self.assertNotIn("Barangay Priority Comparison", response.text)
        account_query.assert_not_called()
        review_query.assert_not_called()
        import_query.assert_not_called()
        barangay_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
