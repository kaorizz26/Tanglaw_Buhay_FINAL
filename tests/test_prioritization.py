"""Staff prioritization checks use mocked accounts and synthetic stored summaries."""

from copy import deepcopy
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from services.assessment_queries import AssessmentQueryError
from services import prioritization


def summary_page(page=1, *, total=51):
    """Build already-stored synthetic fields without invoking assessment engines."""
    start = (page - 1) * 25
    items = []
    for number in range(start, min(total, start + 25)):
        items.append({
            "household_uuid": f"SYNTHETIC-{number:04d}",
            "vulnerability_rank": 1 if number < 2 else number + 1,
            "vulnerability_score": 50.0 if number < 2 else 25.0,
            "coverage_pct": 100.0,
            "unknown_indicator_count": 0,
            "data_quality_status": "Complete Data",
            "program_statuses": {
                "4Ps": "For Review" if number else "No Historical CBMS Trigger",
                "SLP": "Needs Data Review",
                "SPISC": "No Historical CBMS Trigger",
                "TUPAD": "For Review" if number else "No Historical CBMS Trigger",
            },
            "identity": {
                "source_row_number": total - number,
                "head_count": 1,
                "first_name": "Synthetic",
                "middle_name": "",
                "last_name": f"Head {number}",
                "barangay": "Synthetic Barangay",
            },
        })
    return {
        "context": {"run_id": 2, "survey_year": 2022, "municipality": "Pozorrubio"},
        "items": items,
        "page": page,
        "page_size": 25,
        "total": total,
    }


class PrioritizationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": token_urlsafe(48),
            "DATABASE_URL": None,
        })
        self.client = self.app.test_client()
        self.user = {
            "id": 1, "username": "synthetic_staff", "role": "lgu_staff",
            "is_active": True, "session_version": 1,
        }
        self.csrf = token_urlsafe(32)
        patches = (
            patch("psycopg.connect", side_effect=AssertionError("No database connections")),
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch(
                "repositories.staff_users.find_by_id",
                side_effect=lambda *args: dict(self.user),
            ),
            patch.object(
                prioritization, "list_active_assessment_summaries",
                side_effect=lambda **kwargs: summary_page(kwargs["page"]),
            ),
        )
        for patcher in patches:
            mock = patcher.start()
            self.addCleanup(patcher.stop)
        self.query = mock

    def _session(self):
        with self.client.session_transaction() as session:
            session.update(
                user_id=1, session_version=1, issued_at=int(time()), csrf_token=self.csrf,
            )

    def test_anonymous_inactive_revoked_and_unknown_roles_cannot_query_records(self):
        self.assertEqual(self.client.get("/assessments").location, "/login")
        for field, value in (
            ("is_active", False), ("session_version", 2), ("role", "unapproved"),
        ):
            with self.subTest(field=field), patch.dict(self.user, {field: value}):
                self._session()
                self.assertEqual(self.client.get("/assessments").location, "/login")
        self.query.assert_not_called()

    def test_both_roles_render_seven_columns_and_only_for_review_badges(self):
        for role, label in (
            ("lgu_staff", "LGU Staff"),
            ("system_administrator", "System Administrator"),
        ):
            with self.subTest(role=role), patch.dict(self.user, role=role):
                self._session()
                response = self.client.get("/assessments")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                stylesheets = [
                    response.text.index(f'href="/static/css/{name}.css"')
                    for name in ("base", "staff", "assessments")
                ]
                self.assertEqual(stylesheets, sorted(stylesheets))
                self.assertEqual(response.text.count('scope="col"'), 7)
                self.assertEqual(response.text.count('scope="row"'), 25)
                self.assertIn(label, response.text)
                self.assertIn("synthetic_staff", response.text)
                self.assertIn("Assessment Run 2", response.text)
                self.assertIn("Household ID", response.text)
                self.assertIn("Household Head", response.text)
                self.assertIn("not official", response.text)
                self.assertIn("No For Review signals", response.text)
                self.assertIn('class="subsidy-badge">4Ps', response.text)
                self.assertIn('class="subsidy-badge">TUPAD', response.text)
                self.assertNotIn('class="subsidy-badge">SLP', response.text)
                self.assertNotIn('class="subsidy-badge">SPISC', response.text)
                self.assertNotIn('href="/households', response.text)
                self.assertNotIn('href="/assessments/SYNTHETIC', response.text)
                self.assertNotIn('<input type="search"', response.text)
        self.query.assert_called_with(
            page=1, page_size=25, include_household_identity=True,
        )
        for name in ("base", "staff", "assessments"):
            with self.client.get(f"/static/css/{name}.css") as stylesheet:
                self.assertEqual(stylesheet.status_code, 200)

    def test_pagination_links_and_counts_include_last_partial_page(self):
        self._session()
        first = self.client.get("/assessments").text
        self.assertIn('href="/assessments?page=2"', first)
        self.assertNotIn('rel="prev"', first)
        self.assertIn("1–25", first)
        self.assertIn('class="household-reference-id">HH-00051', first)
        second = self.client.get("/assessments?page=2").text
        self.assertIn("26–50", second)
        self.assertIn('class="household-reference-id">HH-00026', second)
        self.assertIn('rel="prev"', second)
        last = self.client.get("/assessments?page=3").text
        self.assertIn("51–51", last)
        self.assertIn("Page 3 of 3", last)
        self.assertNotIn('rel="next"', last)
        self.assertEqual(last.count('scope="row"'), 1)
        self.assertIn('class="household-reference-id">HH-00001', last)
        self.assertEqual(self.client.get("/assessments?page=4").status_code, 404)

    def test_invalid_page_arguments_fail_without_querying(self):
        self._session()
        for value in ("0", "-1", "1.5", "abc", "", "999999999999999", "1&page=2", "%D9%A1"):
            with self.subTest(value=value):
                self.assertEqual(self.client.get(f"/assessments?page={value}").status_code, 400)
        self.query.assert_not_called()

    def test_reference_ids_use_stored_rows_across_pages_and_reordered_subsets(self):
        all_rows = []
        for page in (1, 2, 3):
            result = prioritization.get_prioritization_page(page)
            all_rows.extend(result["items"])
        expected = {
            f"SYNTHETIC-{number:04d}": f"HH-{51 - number:05d}"
            for number in range(51)
        }
        self.assertEqual(
            {row["household_uuid"]: row["household_reference_id"] for row in all_rows},
            expected,
        )
        # Future filtered subsets must use source positions, never page offsets.
        source = summary_page()
        source["items"] = [source["items"][20], source["items"][0]]
        self.query.side_effect = None
        self.query.return_value = source
        subset = prioritization.get_prioritization_page(1)["items"]
        self.assertEqual(
            [row["household_reference_id"] for row in subset],
            ["HH-00031", "HH-00051"],
        )
        self.assertEqual([row["vulnerability_rank"] for row in subset], [21, 1])
        self.assertEqual(prioritization._household_reference_id(1), "HH-00001")
        self.assertEqual(prioritization._household_reference_id(19304), "HH-19304")

    def test_invalid_source_row_numbers_fail_safely_without_fake_ids(self):
        self._session()
        for position in (None, 0, -1, True, "1", 1.5):
            source = summary_page(total=1)
            source["items"][0]["identity"]["source_row_number"] = position
            self.query.side_effect = None
            self.query.return_value = source
            with self.subTest(position=position):
                response = self.client.get("/assessments")
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("HH-", response.text)

    def test_missing_assessment_is_sanitized_without_fixture_fallback(self):
        self._session()
        self.query.side_effect = AssessmentQueryError("SYNTHETIC_PRIVATE_DATABASE_DETAIL")
        response = self.client.get("/assessments")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("SYNTHETIC_PRIVATE", response.text)
        self.assertNotIn("ranking-table", response.text)

    def test_rendering_escapes_names_identifiers_and_barangays(self):
        source = summary_page(total=1)
        source["items"][0]["identity"].update(
            first_name="<script>alert(1)</script>", barangay="<b>test</b>",
        )
        source["items"][0]["household_uuid"] = '<img src=x onerror="alert(1)">'
        self.query.side_effect = None
        self.query.return_value = source
        self._session()
        response = self.client.get("/assessments")
        self.assertNotIn("<script>alert", response.text)
        self.assertNotIn("<b>test</b>", response.text)
        self.assertNotIn('<img src=x', response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_shared_layout_keeps_csrf_logout_and_fixture_landing_boundary(self):
        self._session()
        response = self.client.get("/assessments")
        self.assertIn(f'name="csrf_token" value="{self.csrf}"', response.text)
        self.assertIn('action="/logout"', response.text)
        self.assertEqual(self.client.post("/logout").status_code, 400)
        with patch("repositories.staff_users.invalidate_sessions") as revoke:
            self.assertEqual(
                self.client.post("/logout", data={"csrf_token": self.csrf}).location,
                "/login",
            )
            revoke.assert_called_once()
        self._session()
        with patch.dict(self.app.extensions["development_workspace"], source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/").status_code, 403)
            self.assertEqual(self.client.get("/assessments").status_code, 200)
            self.assertEqual(self.client.get("/test").status_code, 200)
        self.assertEqual(self.client.get("/assessments/SYNTHETIC").status_code, 410)
        self.assertEqual(self.client.get("/assessments?view=queue").status_code, 410)

    def test_presentation_preserves_values_ties_and_source_objects(self):
        source = summary_page()
        before = deepcopy(source)
        self.query.side_effect = None
        self.query.return_value = source
        result = prioritization.get_prioritization_page(1)
        self.assertEqual(source, before)
        self.assertEqual([row["vulnerability_rank"] for row in result["items"][:3]], [1, 1, 3])
        self.assertEqual(result["items"][0]["vulnerability_score"], 50.0)
        self.assertEqual(result["items"][0]["household_uuid"], "SYNTHETIC-0000")
        self.assertEqual(result["items"][1]["review_programs"], ["4Ps", "TUPAD"])
        source["items"][0]["identity"].update(
            first_name="Na", middle_name="NA", last_name="Synthetic",
        )
        self.assertEqual(
            prioritization.get_prioritization_page(1)["items"][0]["head_name"],
            "Na Synthetic",
        )
        for count, expected in (
            (0, "Household head not recorded"),
            (2, "Household head needs verification"),
        ):
            source["items"][0]["identity"]["head_count"] = count
            display = prioritization.get_prioritization_page(1)
            self.assertEqual(display["items"][0]["head_name"], expected)
        self.assertEqual(
            prioritization._page_numbers(387, 773),
            [1, None, 386, 387, 388, None, 773],
        )


if __name__ == "__main__":
    unittest.main()
