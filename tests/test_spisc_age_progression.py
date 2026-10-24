"""Synthetic age progression checks; no database or private source access."""

from copy import deepcopy
from datetime import date, datetime
import unittest
from unittest.mock import patch

from services.screening.spisc import screen_spisc
from services.spisc_age_progression import (
    classify_senior_age, summarize_household_senior_age,
)


class SpiscAgeProgressionTests(unittest.TestCase):
    def test_2022_ages_on_october_24_2026(self):
        for age, category in (
            (54, "below_senior_age"), (55, "possible_senior_age"),
            (56, "reached_senior_age"), (57, "reached_senior_age"),
            (59, "reached_senior_age"), (60, "recorded_senior"),
            (62, "recorded_senior"),
        ):
            with self.subTest(age=age):
                self.assertEqual(classify_senior_age(age, 2022, date(2026, 10, 24)), {
                    "category": category, "minimum_age": age + 4,
                    "maximum_age": age + 5,
                })

    def test_2024_batch_in_2027(self):
        for age, category in (
            (55, "below_senior_age"), (56, "possible_senior_age"),
            (57, "possible_senior_age"), (58, "reached_senior_age"),
            (59, "reached_senior_age"), (60, "recorded_senior"),
        ):
            with self.subTest(age=age):
                result = classify_senior_age(age, 2024, date(2027, 10, 24))
                self.assertEqual(result, {
                    "category": category, "minimum_age": age + 2,
                    "maximum_age": age + 4,
                })

    def test_calendar_boundaries(self):
        for assessed, bounds, category in (
            (date(2026, 1, 1), (58, 59), "below_senior_age"),
            (date(2026, 1, 2), (58, 59), "below_senior_age"),
            (date(2026, 6, 30), (59, 59), "below_senior_age"),
            (date(2026, 7, 1), (59, 60), "possible_senior_age"),
            (date(2026, 12, 31), (59, 60), "possible_senior_age"),
            (date(2027, 12, 31), (60, 61), "reached_senior_age"),
        ):
            with self.subTest(assessed=assessed):
                result = classify_senior_age(55, 2022, assessed)
                self.assertEqual((result["minimum_age"], result["maximum_age"]), bounds)
                self.assertEqual(result["category"], category)

    def test_same_year_and_leap_day(self):
        self.assertEqual(classify_senior_age(59, 2024, date(2024, 2, 29)), {
            "category": "possible_senior_age", "minimum_age": 59, "maximum_age": 60,
        })
        self.assertEqual(classify_senior_age(59, 2024, date(2024, 1, 1)), {
            "category": "below_senior_age", "minimum_age": 59, "maximum_age": 59,
        })
        self.assertEqual(classify_senior_age(0, 2024, date(2024, 12, 31)), {
            "category": "below_senior_age", "minimum_age": 0, "maximum_age": 1,
        })

    def test_future_year_is_not_hard_coded(self):
        self.assertEqual(
            classify_senior_age(57, 2030, date(2034, 10, 24))["category"],
            "reached_senior_age",
        )

    def test_verified_future_reference_and_leap_day(self):
        self.assertEqual(classify_senior_age(
            56, 2024, date(2027, 10, 24), reference_date=date(2024, 12, 31),
        ), {'category': 'below_senior_age', 'minimum_age': 58, 'maximum_age': 59})
        for assessed, bounds in ((date(2028, 2, 28), (17, 17)),
                                 (date(2028, 2, 29), (17, 17)),
                                 (date(2028, 3, 1), (17, 18))):
            result = classify_senior_age(
                13, 2024, assessed, reference_date=date(2024, 2, 29),
            )
            self.assertEqual((result['minimum_age'], result['maximum_age']), bounds)
        for reference in ('2024-06-30', date(2023, 6, 30), datetime(2024, 6, 30)):
            with self.assertRaises(ValueError):
                classify_senior_age(55, 2024, date(2027, 10, 24), reference_date=reference)
        with self.assertRaises(ValueError):
            classify_senior_age(55, 2022, date(2022, 6, 29))
        with self.assertRaises(ValueError):
            classify_senior_age(55, 2022, date(2026, 10, 24), reference_date=date(2022, 1, 1))

    def test_missing_and_invalid_ages(self):
        for age in (None, "", "NA", "bad", True, False, -1, 59.5,
                    float("nan"), float("inf"), [], {}):
            with self.subTest(age=age):
                self.assertEqual(classify_senior_age(age, 2022, date(2026, 10, 24)), {
                    "category": "unknown_age", "minimum_age": None, "maximum_age": None,
                })
        for age in (60, 60.0, "60", "60.0"):
            self.assertEqual(
                classify_senior_age(age, 2022, date(2026, 10, 24))["category"],
                "recorded_senior",
            )

    def test_invalid_context_is_rejected_even_without_ages(self):
        for year in (None, True, "2022", 2022.0, 0, -1, 10000):
            with self.subTest(year=year), self.assertRaises(ValueError):
                classify_senior_age(None, year, date(2026, 10, 24))
        for assessed in (None, "2026-10-24", "2026-02-30", 2026,
                         datetime(2026, 10, 24), date(2021, 12, 31)):
            with self.subTest(assessed=assessed), self.assertRaises(ValueError):
                summarize_household_senior_age({"uuid": "A"}, [], 2022, assessed)

    def test_summary_members_and_unique_relevant_lines(self):
        members = [
            {"uuid": "A", "line_number": line, "a07_age": age, "l16_pwd": 1}
            for line, age in ((1, 60), (2, 57), (3, 55), (4, 30), (5, None))
        ]
        result = summarize_household_senior_age(
            {"uuid": "A", "l15_with_pwd_member": 1},
            members + [deepcopy(members[0])], 2022, date(2026, 10, 24),
        )
        self.assertEqual(result["relevant_member_line_numbers"], (1, 2, 3))
        self.assertEqual(len(result["members"]), 6)
        self.assertTrue(result["has_unknown_age"])
        self.assertEqual(result["household_uuid"], "A")
        self.assertEqual(result["survey_year"], 2022)
        self.assertEqual(result["assessment_date"], date(2026, 10, 24))

    def test_empty_roster_and_known_negative(self):
        for members, unknown in (([], True), ([{
            "uuid": "A", "line_number": 1, "a07_age": 30,
        }], False)):
            result = summarize_household_senior_age(
                {"uuid": "A"}, members, 2022, date(2026, 10, 24),
            )
            self.assertEqual(result["relevant_member_line_numbers"], ())
            self.assertEqual(result["has_unknown_age"], unknown)

    def test_foreign_household_member_is_rejected(self):
        with self.assertRaises(ValueError):
            summarize_household_senior_age(
                {"uuid": "A"}, [{"uuid": "B", "line_number": 1, "a07_age": 60}],
                2022, date(2026, 10, 24),
            )

    def test_no_mutation_or_historical_status_change(self):
        for pwd in (1, 2, None):
            household = {"uuid": "A", "l15_with_pwd_member": pwd}
            members = [{"uuid": "A", "line_number": 1, "a07_age": 57,
                        "l16_pwd": pwd, "section_l_line_number": 1}]
            original = deepcopy((household, members))
            historical = screen_spisc(household, members, 2022)
            with patch("psycopg.connect", side_effect=AssertionError("No database access")):
                summary = summarize_household_senior_age(
                    household, members, 2022, date(2026, 10, 24),
                )
            self.assertEqual((household, members), original)
            self.assertEqual(screen_spisc(household, members, 2022), historical)
            self.assertEqual(historical["screening_status"], "No Historical CBMS Trigger")
            self.assertEqual(summary["members"][0]["category"], "reached_senior_age")
            self.assertNotIn("screening_status", summary)


if __name__ == "__main__":
    unittest.main()
