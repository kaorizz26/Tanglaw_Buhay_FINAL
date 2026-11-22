"""Citizen-safe presentation of stored household screening and review records."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from assessment_detail_fixtures import detail_source, screening_source
from services.citizen_household import (
    CitizenHouseholdError,
    build_citizen_updates,
    get_citizen_household,
    prepare_citizen_household,
)


ACCOUNT = {
    "id": 41,
    "batch_id": 1,
    "household_uuid": "00000000-0000-4000-8000-000000000001",
}


def linked_source(*, detailed=False):
    """Return explicit stored synthetic data linked to the synthetic account."""
    source = deepcopy(screening_source() if detailed else detail_source())
    source["citizen_account"] = {"id": ACCOUNT["id"]}
    source["staff_reviews"] = []
    return source


def review(**changes):
    saved_at = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
    row = {
        "id": 12,
        "current_review_id": 12,
        "program": "4Ps",
        "status": "Closed",
        "withdrawn": False,
        "effective_recommendation": "For LGU Follow-Up",
        "effective_explanation": "Please coordinate with the LGU office for the next review step.",
        "created_at": saved_at,
        "finished_at": saved_at,
        "cancelled_at": None,
        "effective_at": saved_at,
        # These records must never be copied into the citizen presentation.
        "notes": [{"body": "INTERNAL STAFF NOTE"}],
        "starting_reason": "INTERNAL STARTING REASON",
    }
    row.update(changes)
    return row


class CitizenHouseholdPresentationTests(unittest.TestCase):
    def test_presents_four_stored_programs_without_internal_values(self):
        page = prepare_citizen_household(linked_source(detailed=True), ACCOUNT)

        self.assertEqual(page["state"], "ready")
        self.assertEqual([item["code"] for item in page["programs"]],
                         ["4Ps", "SLP", "SPISC", "TUPAD"])
        self.assertTrue(all(item["screening_label"] for item in page["programs"]))
        shown = repr(page)
        for hidden in ("INTERNAL_TECHNICAL", "synthetic", "pilot_assumption"):
            self.assertNotIn(hidden, shown)

    def test_completed_review_is_separate_and_only_its_feedback_is_shown(self):
        source = linked_source()
        source["staff_reviews"] = [review()]

        page = prepare_citizen_household(source, ACCOUNT)
        four_ps = next(item for item in page["programs"] if item["code"] == "4Ps")

        self.assertEqual(four_ps["review"]["status_label"], "For LGU Follow-Up")
        self.assertIn("coordinate with the LGU", four_ps["review"]["feedback"])
        shown = repr(four_ps)
        self.assertNotIn("INTERNAL STAFF NOTE", shown)
        self.assertNotIn("INTERNAL STARTING REASON", shown)
        self.assertNotIn("Approved", shown)
        self.assertNotIn("Eligible", shown)

    def test_active_review_is_under_review_not_approved(self):
        source = linked_source()
        source["staff_reviews"] = [review(
            status="In Review", effective_recommendation=None,
            effective_explanation=None, effective_at=None, finished_at=None,
        )]

        page = prepare_citizen_household(source, ACCOUNT)
        four_ps = next(item for item in page["programs"] if item["code"] == "4Ps")
        self.assertEqual(four_ps["review"]["status_label"], "Under Review")
        self.assertIsNone(four_ps["review"]["feedback"])

    def test_account_batch_household_and_id_must_all_match(self):
        for changed in (
            {**ACCOUNT, "id": 99},
            {**ACCOUNT, "batch_id": 2},
            {**ACCOUNT, "household_uuid": "another-household"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(CitizenHouseholdError):
                    prepare_citizen_household(linked_source(), changed)

    @patch("services.citizen_household.get_active_assessment_detail")
    def test_lookup_uses_only_the_household_from_the_signed_in_account(self, read_detail):
        read_detail.return_value = linked_source()
        page = get_citizen_household(ACCOUNT)
        self.assertEqual(page["state"], "ready")
        read_detail.assert_called_once_with(
            ACCOUNT["household_uuid"], include_staff_reviews=True,
        )

    def test_updates_are_newest_first_and_use_citizen_visible_statuses(self):
        older = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
        newer = datetime(2026, 11, 20, 6, 0, tzinfo=timezone.utc)
        source = linked_source()
        source["staff_reviews"] = [review(effective_at=older, finished_at=older)]
        household = prepare_citizen_household(source, ACCOUNT)
        documents = [{
            "title": "Proof of residency",
            "status": "needs_correction",
            "status_label": "Needs Correction",
            "created_at": older,
            "updated_at": newer,
        }]

        updates = build_citizen_updates(household, documents)
        self.assertEqual(updates[0]["title"], "Proof of residency")
        self.assertIn("Needs Correction", updates[0]["message"])
        self.assertEqual(updates[1]["title"], "4Ps staff review")


if __name__ == "__main__":
    unittest.main()
