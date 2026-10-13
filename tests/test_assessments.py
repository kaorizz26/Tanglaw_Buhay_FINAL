"""Unified-output preservation and fixture-only HTTP integration checks."""

from copy import deepcopy
from html.parser import HTMLParser
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import pandas as pd

from app import create_app
from services.assessments import build_unified_assessments, combine_assessments
from services.assessment_views import listing_rows, member_name
from services.development_data import development_sources
from services.subsidy_screening import PROGRAMS, screen_households
from services.vulnerability.scoring import INDICATOR_WEIGHTS, assess_households


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


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

    def test_missing_name_parts_are_not_invented(self):
        self.assertEqual(member_name({"a01_first_name": pd.NA, "a01_last_name": "NA"}), "Name not recorded")
        self.assertEqual(member_name({"a01_first_name": "Sample", "a01_middle_name": float("nan")}), "Sample")


class StaffInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("pandas.read_excel", side_effect=AssertionError("UI must not read CBMS Excel")), patch("services.data_loader.load_data", side_effect=AssertionError("No actual CBMS loading")):
            cls.app = create_app({"TESTING": True, "DATABASE_URL": None,
                                  "SECRET_KEY": "synthetic-interface-key-only-32-characters"})
        cls.workspace = cls.app.extensions["development_workspace"]

    def setUp(self):
        self.client = self.app.test_client()
        # Authenticate presentation tests without introducing a database dependency.
        self.user_loader = patch("services.auth.load_session_user")
        loader = self.user_loader.start()
        self.addCleanup(self.user_loader.stop)

        def synthetic_staff():
            from flask import g
            g.user = {"id": 1, "role": "lgu_staff"}

        loader.side_effect = synthetic_staff

    def test_initial_routes_and_all_household_sections_render(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/assessments"))
        for path in ("/households", "/households/DEV-0001", "/assessments", "/assessments?view=queue", *[f"/assessments/DEV-0001?tab={tab}" for tab in ("overview", "mpi", "subsidy", "history")]):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Development fixtures", response.get_data(as_text=True))
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_requests_never_recalculate_or_read_actual_data(self):
        with patch("services.assessments.assess_households", side_effect=AssertionError("Do not recalculate on HTTP")), patch("services.assessments.screen_households", side_effect=AssertionError("Do not screen on HTTP")), patch("services.data_loader.load_data", side_effect=AssertionError("No source loading")):
            for path in ("/assessments", "/households", "/assessments/DEV-0001?tab=mpi", "/households/DEV-0001"):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_search_pagination_and_filters_are_functional(self):
        response = self.client.get("/households?q=DEV-0032").get_data(as_text=True)
        self.assertIn("DEV-0032", response)
        self.assertNotIn("DEV-0001", response)
        page = self.client.get("/households?page=2").get_data(as_text=True)
        self.assertIn("DEV-0011", page)
        self.assertNotIn("DEV-0001", page)
        empty = self.client.get("/assessments?q=NOT-IN-BATCH").get_data(as_text=True)
        self.assertIn("No matching assessments", empty)
        for value in ("bad", "0", "-1"):
            self.assertEqual(self.client.get("/households?page=" + value).status_code, 400)

    def test_filters_sorting_and_ties_keep_full_batch_ranks(self):
        ranks = {uuid: row["mpi"]["vulnerability_rank"] for uuid, row in self.workspace["results"].items()}
        for program in PROGRAMS:
            for sort in ("rank", "score_asc", "coverage", "id"):
                rows = listing_rows(self.workspace, program=program, sort=sort)
                for row in rows:
                    uuid = row["profile"]["uuid"]
                    self.assertEqual(row["result"]["mpi"]["vulnerability_rank"], ranks[uuid])
                    self.assertEqual(row["result"]["programs"][program]["screening_status"], "For Review")
                if sort in ("rank", "score_asc"):
                    scores = [row["result"]["mpi"]["vulnerability_score"] for row in rows]
                    self.assertEqual(scores, sorted(scores, reverse=sort == "rank"))
        for quality in ("complete", "needs_review"):
            rows = listing_rows(self.workspace, quality=quality)
            self.assertTrue(all(bool(row["result"]["mpi"]["unknown_indicator_count"]) == (quality == "needs_review") for row in rows))

    def test_all_navigation_targets_and_profile_anchors_exist(self):
        for path in ("/assessments", "/households/DEV-0001", "/assessments/DEV-0001", "/assessments/DEV-0001?tab=mpi", "/assessments/DEV-0001?tab=subsidy", "/assessments?view=queue"):
            parser = PageLinks()
            parser.feed(self.client.get(path).get_data(as_text=True))
            for link in set(parser.links):
                if link.startswith("#"):
                    self.assertIn(link[1:], parser.ids)
                elif not urlparse(link).scheme:
                    self.assertEqual(self.client.get(link).status_code, 200, link)

    def test_four_dimensions_sixteen_indicators_and_four_program_panels(self):
        mpi = self.client.get("/assessments/DEV-0001?tab=mpi").get_data(as_text=True)
        self.assertEqual(mpi.count('class="dimension"'), 4)
        self.assertEqual(mpi.count('class="indicator"'), 16)
        for program in PROGRAMS:
            html = self.client.get("/assessments/DEV-0001?tab=subsidy&program=" + program).get_data(as_text=True)
            self.assertEqual(html.count('role="tab"'), 4)
            self.assertEqual(html.count('role="tabpanel"'), 4)
            self.assertIn(f'data-program-select="{program}"', html)

    def test_private_record_lookups_and_nonfixture_sources_are_blocked(self):
        for path in ("/households/unknown-private-uuid", "/assessments/unknown-private-uuid"):
            self.assertEqual(self.client.get(path).status_code, 404)
        with patch.dict(self.workspace, source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/households").status_code, 403)
            self.assertEqual(self.client.get("/assessments/DEV-0001").status_code, 403)

    def test_invalid_options_and_review_writes_are_not_supported(self):
        for path in ("/assessments?program=invalid", "/assessments?sort=invalid", "/assessments?view=invalid", "/assessments/DEV-0001?tab=invalid"):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.assertEqual(self.client.post("/assessments/DEV-0001").status_code, 405)
        history = self.client.get("/assessments/DEV-0001?tab=history").get_data(as_text=True)
        self.assertIn("Staff reviews are not connected yet", history)
        self.assertNotIn("<form", history)

    def test_every_fixture_displays_its_own_score_and_coverage(self):
        for uuid, result in self.workspace["results"].items():
            html = self.client.get(f"/assessments/{uuid}").get_data(as_text=True)
            self.assertIn(f'class="large-score">{result["mpi"]["vulnerability_score"]:.2f}', html)
            self.assertIn(f'class="coverage-value">{result["mpi"]["coverage_pct"]:.2f}', html)
            self.assertIn(f'#{result["mpi"]["vulnerability_rank"]}', html)

    def test_source_text_is_escaped_in_html(self):
        profile = self.workspace["profiles"]["DEV-0001"]
        with patch.dict(profile, head='<script>alert("test")</script>'):
            html = self.client.get("/households/DEV-0001").get_data(as_text=True)
        self.assertNotIn('<script>alert("test")</script>', html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
