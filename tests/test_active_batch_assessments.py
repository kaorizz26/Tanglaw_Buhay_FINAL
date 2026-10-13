"""Focused synthetic checks for aggregate-only full-batch verification."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from scripts.verify_active_batch_assessments import VerificationError, verify_outputs
from services.development_data import development_sources
from services.subsidy_screening import screen_households
from services.vulnerability.scoring import INDICATOR_WEIGHTS, assess_households


class AssessmentVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("psycopg.connect", side_effect=AssertionError("No database connections")):
            cls.datasets, cls.references = development_sources()
            cls.mpi = assess_households(cls.datasets, cls.references["mpi_mapping"])
            cls.screening = screen_households(cls.datasets, survey_year=2022)

    def setUp(self):
        guard = patch("psycopg.connect", side_effect=AssertionError("No database connections"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_valid_outputs_are_aggregate_only_and_not_modified(self):
        mpi_before = self.mpi.copy(deep=True)
        screening_before = deepcopy(self.screening)
        summary = verify_outputs(self.datasets, self.mpi, self.screening, 2022)
        self.assertEqual(summary["unified_households"], 32)
        self.assertEqual(summary["screening"]["results"], 128)
        self.assertEqual(sum(summary["mpi"]["score_histogram"].values()), 32)
        self.assertGreater(summary["mpi"]["unknown_indicator_events"], 0)
        for counts in summary["screening"]["program_status_counts"].values():
            self.assertEqual(sum(counts.values()), 32)
        public_output = json.dumps(summary)
        self.assertNotIn("DEV-", public_output)
        self.assertNotIn("Sample Barangay", public_output)
        self.assertNotIn("observed_values", public_output)
        self.assertNotIn("member_line_number", public_output)
        self.assertTrue(self.mpi.equals(mpi_before))
        self.assertTrue(self.screening.equals(screening_before))

    def test_tampered_score_or_competition_rank_is_rejected(self):
        for column in ("vulnerability_score", "vulnerability_rank"):
            results = self.mpi.copy(deep=True)
            results.loc[results.index[0], column] += 1
            with self.subTest(column=column):
                with self.assertRaises(VerificationError):
                    verify_outputs(self.datasets, results, self.screening, 2022)

    def test_silently_replacing_unknown_with_zero_is_rejected(self):
        results = self.mpi.copy(deep=True)
        name = next(name for name in INDICATOR_WEIGHTS if results[name].isna().any())
        position = results.index[results[name].isna()][0]
        results.loc[position, name] = 0
        with self.assertRaises(VerificationError):
            verify_outputs(self.datasets, results, self.screening, 2022)

    def test_missing_duplicate_and_wrong_year_screenings_are_rejected(self):
        duplicate = self.screening.copy(deep=True)
        duplicate.iloc[1] = duplicate.iloc[0]
        wrong_year = self.screening.copy(deep=True)
        wrong_year["survey_year"] = 2023
        for results in (self.screening.iloc[:-1], duplicate, wrong_year):
            with self.assertRaises(VerificationError):
                verify_outputs(self.datasets, self.mpi, results, 2022)

    def test_invalid_status_and_null_evidence_are_rejected(self):
        for column, value in (("screening_status", "PRIVATE_VALUE"), ("supporting_evidence", None)):
            results = self.screening.copy(deep=True)
            results.at[results.index[0], column] = value
            with self.assertRaises(VerificationError) as raised:
                verify_outputs(self.datasets, self.mpi, results, 2022)
            self.assertNotIn("PRIVATE_VALUE", str(raised.exception))

    def test_missing_household_results_are_rejected(self):
        with self.assertRaises(VerificationError):
            verify_outputs(self.datasets, self.mpi.iloc[:-1], self.screening, 2022)


if __name__ == "__main__":
    unittest.main()
