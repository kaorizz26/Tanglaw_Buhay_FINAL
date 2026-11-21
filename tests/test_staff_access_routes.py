"""Administrator HTTP flow for viewing and changing LGU Staff access."""

from datetime import datetime, timezone
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch

from app import create_app
from services.staff_access import PERMISSION_CODES, StaffAccessInputError


def access_result(*, active=True):
    selected = {"dashboard.view", "households.view"}
    permissions = [
        {
            "code": code,
            "label": code.replace(".", " ").title(),
            "description": f"Synthetic description for {code}.",
            "sort_order": index,
            "granted": code in selected,
        }
        for index, code in enumerate(sorted(PERMISSION_CODES), 1)
    ]
    return {
        "account": {
            "id": 7, "display_name": "Synthetic Staff", "username": "synthetic.staff",
            "is_active": active, "session_version": 1,
        },
        "permissions": permissions,
        "selected": frozenset(selected),
        "changed": False,
    }


class StaffAccessRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None,
        })
        self.client = self.app.test_client()
        self.user = {
            "id": 1, "username": "synthetic_admin", "role": "system_administrator",
            "is_active": True, "session_version": 1, "must_change_password": False,
        }
        self.history = {
            "account": access_result()["account"],
            "events": [{
                "id": 1, "permission_code": "households.view",
                "label": "Household Records", "action": "granted",
                "administrator_id": 1, "administrator_name": "Synthetic Administrator",
                "changed_at": datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc),
            }],
        }
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch(
                "repositories.staff_users.find_by_id",
                side_effect=lambda *args: dict(self.user),
            ),
            patch("routes.main.get_staff_access", return_value=access_result()),
            patch("routes.main.get_staff_access_history", return_value=self.history),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sign_in()

    def sign_in(self):
        with self.client.session_transaction() as session:
            session.update(
                user_id=1, session_version=1, issued_at=int(time()),
                csrf_token="synthetic-csrf",
            )

    def test_admin_page_shows_presets_permissions_and_history(self):
        page = self.client.get("/administration/accounts/7/access")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Manage access for Synthetic Staff", page.text)
        self.assertIn("Dashboard only", page.text)
        self.assertIn("Case Reviewer", page.text)
        self.assertIn("Select All", page.text)
        self.assertIn('value="dashboard.view"', page.text)
        self.assertIn("Synthetic Administrator", page.text)
        self.assertIn("Save access", page.text)

    def test_post_is_csrf_protected_and_updates_with_all_selected_codes(self):
        with patch("routes.main.update_staff_access", return_value={"changed": True}) as update:
            self.assertEqual(self.client.post(
                "/administration/accounts/7/access",
                data={"permissions": ["dashboard.view", "households.view"]},
            ).status_code, 400)
            update.assert_not_called()
            response = self.client.post(
                "/administration/accounts/7/access",
                data={
                    "csrf_token": "synthetic-csrf",
                    "permissions": ["dashboard.view", "households.view"],
                },
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, "/administration/accounts/7/access")
        update.assert_called_once_with(
            1, 7, ["dashboard.view", "households.view"],
        )

    def test_invalid_selection_is_redisplayed_without_saving(self):
        with patch(
            "routes.main.update_staff_access",
            side_effect=StaffAccessInputError("Staff Review History requires Assessment Results."),
        ):
            response = self.client.post(
                "/administration/accounts/7/access",
                data={
                    "csrf_token": "synthetic-csrf",
                    "permissions": ["dashboard.view", "reviews.view"],
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("requires Assessment Results", response.text)
        self.assertIn('value="reviews.view"', response.text)

    def test_inactive_account_is_read_only(self):
        with patch("routes.main.get_staff_access", return_value=access_result(active=False)):
            page = self.client.get("/administration/accounts/7/access")
        self.assertEqual(page.status_code, 200)
        self.assertIn("This account is inactive", page.text)
        self.assertNotIn("Save access", page.text)

    def test_lgu_staff_cannot_open_access_manager(self):
        self.user.update(role="lgu_staff", permissions=PERMISSION_CODES)
        self.sign_in()
        self.assertEqual(
            self.client.get("/administration/accounts/7/access").status_code, 403,
        )


if __name__ == "__main__":
    unittest.main()
