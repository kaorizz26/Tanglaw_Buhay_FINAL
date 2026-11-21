"""Direct route enforcement for limited LGU Staff permission sets."""

from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app


HOUSEHOLD_UUID = "11111111-1111-4111-8111-111111111111"


class StaffPermissionRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None,
        })
        self.client = self.app.test_client()
        self.user = {
            "id": 7,
            "username": "limited_staff",
            "display_name": "Limited Staff",
            "role": "lgu_staff",
            "is_active": True,
            "session_version": 1,
            "must_change_password": False,
            "permissions": {"dashboard.view"},
        }
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch(
                "repositories.staff_users.find_by_id",
                side_effect=lambda *args: dict(self.user),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def sign_in(self):
        with self.client.session_transaction() as session:
            session.update(
                user_id=self.user["id"], session_version=1, issued_at=int(time()),
                csrf_token="synthetic-csrf-token",
            )

    def test_dashboard_only_account_reaches_dashboard_but_no_module_routes(self):
        self.sign_in()
        with patch("routes.main.get_active_dashboard_summary", return_value=None), \
                patch("routes.main.get_dashboard_reviews") as reviews:
            dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        reviews.assert_not_called()
        self.assertNotIn('href="/households"', dashboard.text)
        self.assertNotIn('href="/assessments"', dashboard.text)
        self.assertNotIn('href="/submissions/document-requests"', dashboard.text)
        self.assertNotIn("Review Queue", dashboard.text)

        paths = (
            "/households",
            "/assessments",
            "/assessments/queue",
            "/submissions/document-requests",
            "/document-requests/1",
            f"/citizen-accounts/household/{HOUSEHOLD_UUID}/history",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)
                self.assertIn("does not have access", response.text)

    def test_assessment_view_does_not_expose_review_pages_or_actions(self):
        self.user["permissions"] = {"dashboard.view", "assessments.view"}
        self.sign_in()
        assessment = f"/assessments/{HOUSEHOLD_UUID}"
        with patch("routes.assessments.get_staff_review_detail") as review_detail:
            self.assertEqual(
                self.client.get(assessment + "?tab=reviews").status_code, 403,
            )
            self.assertEqual(
                self.client.get(assessment + "/reviews/4Ps").status_code, 403,
            )
        review_detail.assert_not_called()

        captured = {}

        def render(template, **context):
            captured.update(context)
            return "Assessment"

        private_review = {"id": 99, "text": "Private review history"}
        with patch("routes.assessments.get_assessment_detail", return_value={
                "review_history": [private_review], "staff_reviews": [private_review],
        }), patch("routes.assessments.render_template", side_effect=render):
            response = self.client.get(assessment + "?tab=overview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["results"]["review_history"], [])
        self.assertEqual(captured["results"]["staff_reviews"], [])
        self.assertFalse(captured["can_view_reviews"])
        self.assertFalse(captured["can_manage_reviews"])

    def test_review_view_cannot_start_or_change_a_review(self):
        self.user["permissions"] = {
            "dashboard.view", "assessments.view", "reviews.view",
        }
        self.sign_in()
        base = f"/assessments/{HOUSEHOLD_UUID}/reviews/4Ps"
        self.assertEqual(self.client.get(base).status_code, 403)
        self.assertEqual(self.client.get(base + "/1?action=finish").status_code, 403)
        self.assertEqual(self.client.post(
            base + "/1", data={"csrf_token": "synthetic-csrf-token"},
        ).status_code, 403)

    def test_submission_view_cannot_use_management_actions(self):
        self.user["permissions"] = {"dashboard.view", "submissions.view"}
        self.sign_in()
        paths = (
            f"/document-requests/household/{HOUSEHOLD_UUID}/new",
            "/document-requests/1/cancel",
            "/document-requests/1/accept",
            f"/citizen-accounts/household/{HOUSEHOLD_UUID}/new",
            f"/citizen-accounts/household/{HOUSEHOLD_UUID}/deactivate",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_missing_required_dashboard_grant_invalidates_the_session(self):
        self.user["permissions"] = {"households.view"}
        self.sign_in()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")
        with self.client.session_transaction() as session:
            self.assertNotIn("user_id", session)


if __name__ == "__main__":
    unittest.main()
