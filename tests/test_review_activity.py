"""Synthetic review-action history, filtering and privacy-safe export checks."""

from contextlib import nullcontext
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from werkzeug.datastructures import MultiDict

from services import review_activity


def activity_row(**overrides):
    row = {
        "action_reference": "started-42", "review_id": 42,
        "action": "started", "action_at": datetime(2026, 11, 23, tzinfo=timezone.utc),
        "batch_id": 3, "survey_year": 2026, "source_row_number": 8,
        "barangay": "Synthetic East", "program": "4Ps",
        "actor_username": "synthetic_admin", "previous_staff_username": None,
        "responsible_staff_username": None, "recommendation": None,
    }
    row.update(overrides)
    return row


class ReviewActivityFilterTests(unittest.TestCase):
    def test_namespaced_filters_validate_dates_and_preserve_parameters(self):
        filters = review_activity.parse_review_activity_filters(MultiDict([
            ("activity_q", "  HH-00008  "), ("activity_action", "note_added"),
            ("activity_program", "SLP"), ("activity_year", "2026"),
            ("activity_batch", "3"), ("activity_from", "2026-11-01"),
            ("activity_to", "2026-11-30"), ("activity_sort", "oldest"),
            ("activity_page", "2"),
        ]))
        self.assertEqual(filters.household_position, 8)
        self.assertEqual(filters.batch_id, 3)
        self.assertEqual(filters.started_at.utcoffset().total_seconds(), 8 * 60 * 60)
        self.assertEqual(filters.ended_before.day, 1)
        self.assertEqual(filters.parameters["activity_page"], 2)
        self.assertNotIn("activity_page", filters.export_parameters)

    def test_invalid_filters_are_rejected_before_database_access(self):
        invalid = (
            [("activity_action", "deleted")], [("activity_program", "OTHER")],
            [("activity_year", "202")], [("activity_batch", "0")],
            [("activity_page", "0")], [("activity_sort", "sideways")],
            [("activity_from", "2026-12-01"), ("activity_to", "2026-11-01")],
            [("activity_q", "x" * 201)],
            [("activity_action", "started"), ("activity_action", "finished")],
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                    review_activity.ReviewActivityFilterError):
                review_activity.parse_review_activity_filters(MultiDict(values))


class ReviewActivityPresentationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.transaction.return_value = nullcontext()
        self.connect = MagicMock(return_value=self.connection)
        self.filters = review_activity.ReviewActivityFilters()

    def test_page_presents_metadata_without_note_or_reason_contents(self):
        with patch.object(review_activity.review_activity, "count_actions", return_value=1), \
                patch.object(review_activity.review_activity, "list_actions",
                             return_value=[activity_row(action="note_added",
                                                        action_reference="note-7")]), \
                patch.object(review_activity.review_activity, "list_filter_options",
                             return_value=([2026], [{"batch_id": 3, "survey_year": 2026}])):
            result = review_activity.get_review_activity_page(
                self.filters, connection_factory=self.connect,
            )
        self.assertEqual(result["matching_total"], 1)
        self.assertEqual(result["items"][0]["household_reference_id"], "HH-00008")
        self.assertEqual(result["items"][0]["description"], "Note added.")
        self.assertNotIn("body", result["items"][0])

    def test_page_uses_database_limit_and_offset(self):
        filters = review_activity.ReviewActivityFilters(page=2)
        with patch.object(review_activity.review_activity, "count_actions", return_value=80), \
                patch.object(review_activity.review_activity, "list_actions",
                             return_value=[activity_row()]) as listing, \
                patch.object(review_activity.review_activity, "list_filter_options",
                             return_value=([2026], [{"batch_id": 3, "survey_year": 2026}])):
            result = review_activity.get_review_activity_page(
                filters, connection_factory=self.connect,
            )
        listing.assert_called_once_with(self.connection, filters, limit=25, offset=25)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["total_pages"], 4)
        self.assertEqual(result["first_item"], 26)
        self.assertEqual(result["last_item"], 26)
        self.assertTrue(result["has_previous"])
        self.assertTrue(result["has_next"])

    def test_csv_uses_all_matching_rows_and_audits_the_administrator(self):
        row = activity_row(actor_username="=formula", action="finished",
                           recommendation="No Further Action")
        with patch.object(review_activity.review_activity, "list_actions",
                          return_value=[row]) as listing, \
                patch.object(review_activity.review_activity, "record_export") as audit:
            contents = review_activity.export_review_activity(
                self.filters, 7, connection_factory=self.connect,
            )
        listing.assert_called_once_with(self.connection, self.filters)
        audit.assert_called_once_with(
            self.connection, 7, self.filters.audit_payload, 1,
        )
        self.assertIn("Action time (PHT)", contents)
        self.assertIn("'\u003dformula", contents)
        self.assertIn("Recommendation recorded: No Further Action", contents)
        self.assertNotIn("private note", contents)


if __name__ == "__main__":
    unittest.main()
