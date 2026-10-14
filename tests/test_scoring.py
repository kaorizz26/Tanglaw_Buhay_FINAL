"""Focused scoring tests use synthetic flags, without loading CBMS records."""

from fractions import Fraction
from itertools import product
import unittest
from unittest.mock import patch

import pandas as pd

from services.vulnerability.scoring import (
    DIMENSIONS, INDICATOR_WEIGHTS, assess_households, score_indicators,
    sensitivity_summary, validate_weights,
)


class ScoringTests(unittest.TestCase):
    def frames(self, uuids=("A",), value=0, index=None):
        households = pd.DataFrame({"uuid": list(uuids)}, index=index)
        indicators = pd.DataFrame(
            [[value] * 16 for _ in uuids], index=households.index,
            columns=list(INDICATOR_WEIGHTS), dtype=object,
        )
        return households, indicators

    def test_exact_weights_and_four_equal_dimensions(self):
        validate_weights()
        self.assertEqual(len(INDICATOR_WEIGHTS), 16)
        self.assertTrue(all(isinstance(weight, Fraction) for weight in INDICATOR_WEIGHTS.values()))
        self.assertEqual(sum(INDICATOR_WEIGHTS.values(), Fraction(0)), Fraction(1))
        for _, names in DIMENSIONS.values():
            self.assertEqual(sum((INDICATOR_WEIGHTS[name] for name in names), Fraction(0)), Fraction(1, 4))
        self.assertEqual(INDICATOR_WEIGHTS["School Attendance"], Fraction(1, 8))
        self.assertEqual(INDICATOR_WEIGHTS["Food Insecurity"], Fraction(1, 8))
        self.assertEqual(INDICATOR_WEIGHTS["Water"], Fraction(1, 36))
        self.assertEqual(INDICATOR_WEIGHTS["Employment Proxy"], Fraction(1, 12))

    def test_changed_weights_are_rejected(self):
        with patch.dict(INDICATOR_WEIGHTS, {"Water": Fraction(1, 35)}):
            with self.assertRaises(ValueError):
                validate_weights()

    def test_all_zero_scores_and_coverage(self):
        row = score_indicators(*self.frames()).iloc[0]
        self.assertEqual(row["vulnerability_score"], 0)
        self.assertEqual(row["deprived_indicator_count"], 0)
        self.assertEqual(row["not_deprived_indicator_count"], 16)
        self.assertEqual(row["unknown_indicator_count"], 0)
        self.assertEqual(row["known_weight"], 1)
        self.assertEqual(row["coverage_pct"], 100)
        self.assertEqual(row["data_quality_status"], "Complete Data")
        self.assertEqual(row["assumptions_used"], ())
        self.assertEqual(row["vulnerability_rank"], 1)

    def test_all_one_scores(self):
        row = score_indicators(*self.frames(value=1)).iloc[0]
        self.assertEqual(row["vulnerability_score"], 100)
        self.assertEqual(row["deprived_indicator_count"], 16)
        self.assertEqual(row["not_deprived_indicator_count"], 0)
        self.assertEqual(row["unknown_indicator_count"], 0)

    def test_mixed_expected_weighted_score(self):
        households, flags = self.frames()
        for name in ("School Attendance", "Food Insecurity", "Water", "Employment Proxy"):
            flags.at[0, name] = 1
        row = score_indicators(households, flags).iloc[0]
        expected = 100 * (Fraction(1, 8) + Fraction(1, 8) + Fraction(1, 36) + Fraction(1, 12))
        self.assertAlmostEqual(row["vulnerability_score"], float(expected))
        self.assertEqual(row["deprived_indicator_count"], 4)
        self.assertEqual(row["not_deprived_indicator_count"], 12)

    def test_each_unknown_preserves_flag_but_receives_score_and_rank(self):
        for name, weight in INDICATOR_WEIGHTS.items():
            with self.subTest(indicator=name):
                households, flags = self.frames()
                flags.at[0, name] = None
                row = score_indicators(households, flags).iloc[0]
                self.assertIs(row[name], None)
                self.assertEqual(row["vulnerability_score"], 0)
                self.assertEqual(row["vulnerability_rank"], 1)
                self.assertEqual(row["data_quality_status"], "Needs Data Review")
                self.assertEqual(row["unknown_zero_point_indicators"], (name,))
                self.assertIn("Unknown indicators contribute zero points", row["assumptions_used"])
                self.assertEqual(row["unknown_indicator_count"], 1)
                self.assertEqual(row["not_deprived_indicator_count"], 15)
                self.assertAlmostEqual(row["known_weight"], float(1 - weight))
                self.assertAlmostEqual(row["coverage_pct"], float(100 * (1 - weight)))

    def test_unknown_scores_use_original_weights_without_rescaling(self):
        households, flags = self.frames(value=1)
        flags.at[0, "Water"] = None
        row = score_indicators(households, flags).iloc[0]
        self.assertAlmostEqual(row["vulnerability_score"], float(100 * Fraction(35, 36)))
        self.assertLess(row["vulnerability_score"], 100)
        self.assertEqual(row["deprived_indicator_count"], 15)
        self.assertEqual(row["vulnerability_rank"], 1)
        self.assertIs(row["Water"], None)

    def test_all_unknowns_have_zero_coverage_but_one_score_and_rank(self):
        row = score_indicators(*self.frames(value=None)).iloc[0]
        self.assertEqual(row["known_weight"], 0)
        self.assertEqual(row["coverage_pct"], 0)
        self.assertEqual(row["unknown_indicator_count"], 16)
        self.assertEqual(row["vulnerability_score"], 0)
        self.assertEqual(row["vulnerability_rank"], 1)

    def test_pandas_missing_values_remain_unknown(self):
        for missing in (None, pd.NA, float("nan")):
            households, flags = self.frames()
            flags.at[0, "Water"] = missing
            row = score_indicators(households, flags).iloc[0]
            self.assertIs(row["Water"], None)
            self.assertEqual(row["unknown_indicator_count"], 1)
            self.assertEqual(row["vulnerability_score"], 0)

    def test_competition_ties_include_incomplete_households(self):
        households, flags = self.frames(("A", "B", "C", "D", "E"), index=[70, 20, 90, 10, 40])
        flags.loc[70, :] = 1
        flags.loc[90, :] = 1
        flags.at[10, "School Attendance"] = 1
        flags.at[40, "Water"] = None
        result = score_indicators(households, flags)
        self.assertEqual(result["vulnerability_rank"].tolist(), [1, 4, 1, 3, 4])
        self.assertEqual(result["uuid"].tolist(), households["uuid"].tolist())
        self.assertTrue(result.index.equals(households.index))
        self.assertEqual(result["vulnerability_rank"].notna().sum(), 5)
        order = [2, 4, 0, 1, 3]
        shuffled = score_indicators(households.iloc[order], flags.iloc[order])
        self.assertEqual(dict(zip(result["uuid"], result["vulnerability_rank"])), dict(zip(shuffled["uuid"], shuffled["vulnerability_rank"])))

    def test_flag_columns_and_indexes_align_without_mutating_inputs(self):
        households, flags = self.frames(("A", "B"), index=[8, 2])
        flags.at[8, "Water"] = 1
        original_households, original_flags = households.copy(deep=True), flags.copy(deep=True)
        shuffled = flags.iloc[::-1, ::-1]
        result = score_indicators(households, shuffled)
        self.assertTrue(result.index.equals(households.index))
        self.assertEqual(result["Water"].tolist(), [1, 0])
        self.assertAlmostEqual(result.loc[8, "vulnerability_score"], float(100 * Fraction(1, 36)))
        pd.testing.assert_frame_equal(households, original_households)
        pd.testing.assert_frame_equal(flags, original_flags)

    def test_invalid_records_schema_alignment_and_flags_are_rejected(self):
        households, flags = self.frames(("A", "B"))
        with self.assertRaises(ValueError):
            score_indicators(households.assign(uuid="A"), flags)
        with self.assertRaises(ValueError):
            score_indicators(households, flags.set_axis([8, 9]))
        with self.assertRaises(ValueError):
            score_indicators(households, flags.iloc[:1])
        with self.assertRaises(ValueError):
            score_indicators(households, flags.drop(columns="Water"))
        with self.assertRaises(ValueError):
            score_indicators(households, flags.assign(extra_indicator=0))
        with self.assertRaises(ValueError):
            score_indicators(households, pd.concat([flags, flags[["Water"]]], axis=1))
        for value in (True, "1", 2, -1, 0.5):
            bad_flags = flags.copy(deep=True)
            bad_flags.at[0, "Water"] = value
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    score_indicators(households, bad_flags)

    def test_entry_point_reuses_one_indicator_assembly(self):
        households, flags = self.frames()
        members = pd.DataFrame(
            [("A", 1, 30, 7, "NA"), ("A", 2, 40, 1, "NA"), ("A", 3, 70, 7, "NA")],
            columns=["uuid", "line_number", "a07_age", "b06_ofi", "e01_employed"], dtype=object,
        )
        datasets, mapping = {"household": households, "section_a_e_indiv": members}, pd.DataFrame()
        with patch("services.vulnerability.scoring.assemble_indicators", return_value=flags) as assembly:
            result = assess_households(datasets, mapping)
        assembly.assert_called_once_with(datasets, mapping)
        self.assertEqual(len(result), len(households))
        self.assertEqual(result["vulnerability_score"].tolist(), [0])
        self.assertEqual(result["e01_assumed_not_working_count"].tolist(), [0])
        self.assertEqual(result["ofw_assumed_working_count"].tolist(), [1])
        self.assertEqual(result["b06_e01_conflict_count"].tolist(), [0])
        self.assertNotIn("Applicable missing E01 assumed not working", result.iloc[0]["assumptions_used"])
        self.assertIn("Adult B06 1/2/3 with missing E01 assumed working", result.iloc[0]["assumptions_used"])
        self.assertEqual(result.iloc[0]["data_quality_status"], "Complete Data")

    def test_scores_stay_bounded_and_only_one_score_and_rank_are_returned(self):
        for flag in (0, 1, None):
            result = score_indicators(*self.frames(value=flag))
            self.assertTrue(result["vulnerability_score"].between(0, 100).all())
            self.assertEqual([name for name in result if "score" in name], ["vulnerability_score"])
            self.assertEqual([name for name in result if "rank" in name], ["vulnerability_rank"])

    def test_sensitivity_bounds_match_all_small_unknown_completions(self):
        households, flags = self.frames(("A", "B", "C"))
        flags.at[0, "Water"] = None
        flags.at[1, "School Attendance"] = None
        flags.at[2, "Water"] = 1
        result = score_indicators(households, flags)
        possible_ranks = [set() for _ in range(3)]
        for water, attendance in product((0, 1), repeat=2):
            completed = flags.copy(deep=True)
            completed.at[0, "Water"] = water
            completed.at[1, "School Attendance"] = attendance
            ranks = score_indicators(households, completed)["vulnerability_rank"]
            for possibilities, rank in zip(possible_ranks, ranks):
                possibilities.add(rank)
        spans = [max(ranks) - min(ranks) for ranks in possible_ranks]
        summary = sensitivity_summary(result)
        self.assertEqual(summary["uncertain_households"], 2)
        self.assertEqual(summary["households_with_possible_rank_change"], sum(span > 0 for span in spans))
        self.assertEqual(summary["rank_span_max"], max(spans))
        self.assertAlmostEqual(summary["potential_additional_points_min"], float(100 * Fraction(1, 36)))
        self.assertEqual(summary["potential_additional_points_max"], 12.5)
        self.assertEqual(set(summary), {
            "uncertain_households", "potential_additional_points_min",
            "potential_additional_points_median", "potential_additional_points_mean",
            "potential_additional_points_max", "households_with_possible_rank_change",
            "uncertain_households_with_possible_rank_change", "rank_span_median", "rank_span_max",
        })

    def test_sensitivity_without_unknowns_has_no_possible_rank_changes(self):
        result = score_indicators(*self.frames(("A", "B")))
        summary = sensitivity_summary(result)
        self.assertEqual(summary["uncertain_households"], 0)
        self.assertEqual(summary["households_with_possible_rank_change"], 0)
        self.assertEqual(summary["rank_span_max"], 0)


if __name__ == "__main__":
    unittest.main()
