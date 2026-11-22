"""Synthetic checks for the administrator list controls."""

from datetime import datetime, timezone
import unittest
from unittest.mock import ANY, patch

from werkzeug.datastructures import MultiDict

from services.import_history import (
    ImportHistoryFilterError, get_import_history_directory, parse_import_history_filters,
)
from services.staff_account_management import (
    StaffAccountInputError, filter_staff_accounts, parse_staff_account_filters,
    staff_account_status_counts,
)
from services.staff_password_reset_requests import (
    PasswordResetFilterError, filter_password_reset_overview,
    parse_password_reset_filters,
)


class AdminListFilterTests(unittest.TestCase):
    def test_staff_accounts_filter_status_search_and_order(self):
        accounts = [
            {"id": 1, "display_name": "Ana Cruz", "username": "ana", "is_active": True,
             "must_change_password": False, "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)},
            {"id": 2, "display_name": "Ben Reyes", "username": "ben", "is_active": True,
             "must_change_password": True, "created_at": datetime(2024, 2, 1, tzinfo=timezone.utc)},
            {"id": 3, "display_name": "Cara Tan", "username": "cara", "is_active": False,
             "must_change_password": True, "created_at": datetime(2024, 3, 1, tzinfo=timezone.utc)},
        ]
        filters = parse_staff_account_filters(MultiDict((
            ("q", " Ben "), ("status", "pending"), ("sort", "name"),
        )))
        self.assertEqual([item["id"] for item in filter_staff_accounts(accounts, filters)], [2])
        self.assertEqual(staff_account_status_counts(accounts), {
            "ready": 1, "pending": 1, "inactive": 1,
        })
        descending = parse_staff_account_filters(MultiDict((("sort", "name_desc"),)))
        self.assertEqual(
            [item["id"] for item in filter_staff_accounts(accounts, descending)], [3, 2, 1],
        )
        with self.assertRaises(StaffAccountInputError):
            parse_staff_account_filters(MultiDict([("status", "ready"), ("status", "inactive")]))

    def test_reset_requests_filter_completed_and_sort_by_name(self):
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        overview = {
            "pending": [{"id": 1, "staff_id": 4, "username": "ana", "display_name": "Ana",
                         "last_requested_at": now}],
            "completed": [{"id": 3, "username": "zoe", "display_name": "Zoe",
                           "completed_at": now},
                          {"id": 2, "username": "ben", "display_name": "Ben",
                           "completed_at": now}],
        }
        filters = parse_password_reset_filters(MultiDict((
            ("state", "completed"), ("sort", "name"),
        )))
        result = filter_password_reset_overview(overview, filters)
        self.assertEqual(result["pending"], [])
        self.assertEqual([item["username"] for item in result["completed"]], ["ben", "zoe"])
        with self.assertRaises(PasswordResetFilterError):
            parse_password_reset_filters(MultiDict([("sort", "unknown")]))

    def test_import_history_filters_sort_and_page_in_database(self):
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        events = [
            {"id": index, "attempted_at": now, "deleted_at": None,
             "outcome": "staged", "survey_year": 2024,
             "batch_id": index, "file_metadata": {}, "error_count": 0,
             "warning_count": 0, "uploaded_by": "synthetic_admin",
             "action_by": "synthetic_admin", "source_provenance": None}
            for index in range(6, 11)
        ]

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def transaction(self):
                return self

            def execute(self, *args):
                return None

        filters = parse_import_history_filters(MultiDict((
            ("history_outcome", "staged"), ("history_year", "2024"),
            ("history_page", "2"),
        )))
        with (
            patch(
                "services.import_history.import_validation_events.count_history",
                side_effect=(15, 30),
            ) as count_history,
            patch(
                "services.import_history.import_validation_events.list_history_years",
                return_value=[2024, 2022],
            ),
            patch(
                "services.import_history.import_validation_events.list_history_page",
                return_value=events,
            ) as list_page,
        ):
            result = get_import_history_directory(filters, limit=5,
                                                  connection_factory=FakeConnection)
        self.assertEqual(result["matching_total"], 15)
        self.assertEqual(len(result["items"]), 5)
        self.assertTrue(all(item["outcome"] == "staged" for item in result["items"]))
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["total_pages"], 3)
        self.assertEqual((result["first_item"], result["last_item"]), (6, 10))
        self.assertTrue(result["has_previous"])
        self.assertTrue(result["has_next"])
        self.assertEqual(count_history.call_count, 2)
        list_page.assert_called_once_with(
            ANY, search="", outcome="staged", survey_year=2024,
            sort="newest", limit=5, offset=5,
        )

    def test_import_history_page_validation_and_out_of_range_clamping(self):
        for value in ("0", "-1", "two", "1000001"):
            with self.subTest(value=value), self.assertRaises(ImportHistoryFilterError):
                parse_import_history_filters(MultiDict((("history_page", value),)))
        with self.assertRaises(ImportHistoryFilterError):
            parse_import_history_filters(MultiDict((
                ("history_page", "1"), ("history_page", "2"),
            )))

        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def transaction(self):
                return self

            def execute(self, *args):
                return None

        filters = parse_import_history_filters(MultiDict((("history_page", "99"),)))
        event = {
            "id": 26, "attempted_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "deleted_at": None, "outcome": "passed", "survey_year": 2024,
            "batch_id": None, "file_metadata": {}, "error_count": 0,
            "warning_count": 0, "uploaded_by": "synthetic_admin",
            "action_by": "synthetic_admin", "source_provenance": None,
        }
        with (
            patch(
                "services.import_history.import_validation_events.count_history",
                return_value=26,
            ),
            patch(
                "services.import_history.import_validation_events.list_history_years",
                return_value=[],
            ),
            patch(
                "services.import_history.import_validation_events.list_history_page",
                return_value=[event],
            ) as list_page,
        ):
            result = get_import_history_directory(
                filters, limit=25, connection_factory=FakeConnection,
            )
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["total_pages"], 2)
        self.assertTrue(result["has_previous"])
        self.assertFalse(result["has_next"])
        self.assertEqual((result["first_item"], result["last_item"]), (26, 26))
        list_page.assert_called_once_with(
            ANY, search="", outcome="", survey_year=None,
            sort="newest", limit=25, offset=25,
        )

        with self.assertRaises(ImportHistoryFilterError):
            parse_import_history_filters(MultiDict([("history_year", "2023x")]))


if __name__ == "__main__":
    unittest.main()
