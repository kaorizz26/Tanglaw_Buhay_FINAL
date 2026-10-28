"""Indicator pattern presentation uses synthetic counts and strict reconciliation."""

import unittest

from services.dashboard_patterns import DEFAULT_AREA, present_indicator_patterns, select_area
from services.vulnerability.scoring import INDICATOR_WEIGHTS


def raw_counts(households=40, **overrides):
    indicators = {name: {"deprived": 10, "not_deprived": 25, "unknown": 5}
                  for name in INDICATOR_WEIGHTS}
    indicators.update(overrides)
    return {"assessed_households": households, "indicators": indicators}


class IndicatorPatternTests(unittest.TestCase):
    def test_groups_all_sixteen_indicators_into_the_four_areas(self):
        areas = present_indicator_patterns(raw_counts(), 40)
        self.assertEqual([a["name"] for a in areas], [
            "Education", "Health and Nutrition", "Housing, Water and Sanitation", "Employment"])
        self.assertEqual([len(a["indicators"]) for a in areas], [2, 2, 9, 3])
        first = areas[0]["indicators"][0]["outcomes"]
        self.assertEqual([(o["label"], o["count"], o["percent"]) for o in first], [
            ("Deprived", 10, 25.0), ("Not Deprived", 25, 62.5), ("Could Not Determine", 5, 12.5)])

    def test_uses_staff_facing_names(self):
        names = {i["name"] for a in present_indicator_patterns(raw_counts(), 40)
                 for i in a["indicators"]}
        self.assertIn("Illness and Treatment", names)
        self.assertNotIn("Illness/Treatment Proxy", names)

    def test_unreconciled_or_unexpected_counts_are_rejected(self):
        bad = {"deprived": 10, "not_deprived": 25, "unknown": 4}
        with self.assertRaisesRegex(ValueError, "indicator_count_mismatch"):
            present_indicator_patterns(raw_counts(Water=bad), 40)
        with self.assertRaisesRegex(ValueError, "indicator_household_mismatch"):
            present_indicator_patterns(raw_counts(), 41)
        with self.assertRaisesRegex(ValueError, "indicator_count_invalid"):
            present_indicator_patterns(raw_counts(
                Water={"deprived": 10.0, "not_deprived": 25, "unknown": 5}), 40)
        with self.assertRaises(KeyError):
            incomplete = raw_counts()
            del incomplete["indicators"]["Water"]
            present_indicator_patterns(incomplete, 40)

    def test_empty_scope_has_zero_percentages(self):
        zero = {name: {"deprived": 0, "not_deprived": 0, "unknown": 0}
                for name in INDICATOR_WEIGHTS}
        areas = present_indicator_patterns({"assessed_households": 0, "indicators": zero}, 0)
        self.assertTrue(all(o["percent"] == 0 for a in areas for i in a["indicators"]
                            for o in i["outcomes"]))

    def test_area_selection_defaults_safely(self):
        self.assertEqual(select_area("employment"), "employment")
        for value in (None, "", "Employment", "../x", "health,housing"):
            self.assertEqual(select_area(value), DEFAULT_AREA)


if __name__ == "__main__":
    unittest.main()
