"""Backend unified-assessment output preservation with synthetic inputs."""

from copy import deepcopy
import unittest

import pandas as pd

from services.assessments import build_unified_assessments, combine_assessments
from services.development_data import development_sources
from services.subsidy_screening import PROGRAMS, screen_households
from services.vulnerability.scoring import INDICATOR_WEIGHTS, assess_households


class UnifiedAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datasets, cls.references = development_sources()
        cls.mpi = assess_households(cls.datasets, cls.references["mpi_mapping"])
        cls.screening = screen_households(cls.datasets, survey_year=2022)

    def test_every_engine_field_and_nested_result_is_preserved(self):
        source_copies = {name: frame.copy(deep=True) for name, frame in self.datasets.items()}
        results = build_unified_assessments(self.datasets, self.references, survey_year=2022)
        self.assertEqual(list(results), self.mpi["uuid"].tolist())
        self.assertEqual(len(results), 32)
        for mpi_row in self.mpi.to_dict("records"):
            result = results[mpi_row["uuid"]]
            self.assertEqual(result["mpi"], mpi_row)
            self.assertEqual(result["survey_year"], 2022)
            self.assertEqual(list(result["programs"]), list(PROGRAMS))
            self.assertTrue(set(INDICATOR_WEIGHTS).issubset(result["mpi"]))
        for row in self.screening.to_dict("records"):
            self.assertEqual(results[row["household_uuid"]]["programs"][row["program_code"]], row)
        for name, original in source_copies.items():
            pd.testing.assert_frame_equal(self.datasets[name], original)

    def test_join_uses_uuid_instead_of_row_position(self):
        result = combine_assessments(self.mpi.iloc[::-1], self.screening.sample(frac=1, random_state=7), survey_year=2022)
        self.assertEqual(list(result), self.mpi["uuid"].tolist()[::-1])
        for uuid, row in result.items():
            self.assertTrue(all(program["household_uuid"] == uuid for program in row["programs"].values()))

    def test_ambiguous_missing_or_mismatched_results_are_rejected(self):
        duplicate = pd.concat([self.screening, self.screening.iloc[:1]])
        missing = self.screening.iloc[1:]
        orphan = self.screening.copy()
        orphan.loc[orphan.index[0], "household_uuid"] = "NOT-IN-BATCH"
        wrong_year = self.screening.copy()
        wrong_year.loc[wrong_year.index[0], "survey_year"] = 2030
        for frame in (duplicate, missing, orphan, wrong_year):
            with self.subTest(case=len(frame)):
                with self.assertRaises(ValueError):
                    combine_assessments(self.mpi, frame, survey_year=2022)
        for year in (True, 0, "2022", None):
            with self.assertRaises(ValueError):
                combine_assessments(self.mpi, self.screening, survey_year=year)

    def test_combine_does_not_modify_input_outputs(self):
        mpi = self.mpi.copy(deep=True)
        screening = deepcopy(self.screening)
        combine_assessments(mpi, screening, survey_year=2022)
        pd.testing.assert_frame_equal(mpi, self.mpi, check_exact=True)
        pd.testing.assert_frame_equal(screening, self.screening, check_exact=True)


if __name__ == "__main__":
    unittest.main()
