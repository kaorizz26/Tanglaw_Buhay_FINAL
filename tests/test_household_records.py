"""Household route checks use synthetic records and no source database."""

from contextlib import nullcontext
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

from app import create_app
from services import household_records as records
from services.assessment_queries import AssessmentQueryError
from services.household_records import InvalidRecordFilters, RecordFilters, parse_record_arguments
from werkzeug.datastructures import MultiDict


HOUSEHOLD_UUID = UUID("11111111-1111-4111-8111-111111111111")


class HouseholdRecordRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48),
                               "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        for patcher in (
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = {"survey_year": 2022, "municipality": "Pozorrubio"}
        self.directory = {
            "context": self.context, "filters": RecordFilters(),
            "barangays": ["Synthetic East", "Synthetic West"],
            "total": 1, "page": 1, "page_count": 1,
            "range_start": 1, "range_end": 1,
            "items": [{"household_uuid": HOUSEHOLD_UUID, "reference_id": "HH-00001",
                       "head_name": "Synthetic Head", "barangay": "Synthetic East",
                       "member_count": 2}],
        }
        self.record = {
            "context": self.context, "household_uuid": str(HOUSEHOLD_UUID),
            "reference_id": "HH-00001", "head_name": "Synthetic Head",
            "barangay": "Synthetic East",
            "composition": {"total": 2, "children": 1, "adults": 1,
                            "older_adults": 0, "unclear_age": 0},
            "groups": [{"title": "Water & Sanitation", "cells": [
                {"label": "Main water source", "value": "Synthetic answer"}]}],
            "member_columns": ["Age in 2022"],
            "members": [{"line_number": 1, "name": "Synthetic Head", "answers": ["35"]},
                        {"line_number": 2, "name": "Synthetic Child", "answers": ["—"]}],
        }

    def _sign_in(self, role="lgu_staff"):
        self.user["role"] = role
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()),
                           csrf_token=token_urlsafe(32))

    def test_directory_requires_login_and_both_roles_can_read_it(self):
        self.assertEqual(self.client.get("/households").location, "/login")
        self.assertEqual(self.client.get(f"/households/{HOUSEHOLD_UUID}").location, "/login")
        for role in ("lgu_staff", "system_administrator"):
            self._sign_in(role)
            with patch("routes.households.get_record_page", return_value=self.directory) as query:
                response = self.client.get("/households")
            self.assertEqual(response.status_code, 200)
            query.assert_called_once_with(1, RecordFilters())
            self.assertIn("Household Records", response.text)
            self.assertIn("Synthetic Head", response.text)
            self.assertIn("Synthetic East", response.text)
            self.assertIn('href="/households" aria-current="page"', response.text)
            self.assertIn("All barangays", response.text)
            # The survey year comes from the dataset in use and is display-only.
            self.assertIn('id="record-survey-year" type="text" value="2022" readonly', response.text)
            self.assertNotIn('name="year"', response.text)
            self.assertIn('<th scope="col" class="records-members">Members</th>', response.text)
            self.assertNotIn("Clear Filters", response.text)
            # The directory relies on the CBMS year label and Survey Year; no notice banner.
            self.assertNotIn("record-notice", response.text)
            self.assertIn('Cache-Control', response.headers)
            self.assertNotIn(str(HOUSEHOLD_UUID), response.text.split("<main", 1)[0])

    def test_filters_and_return_link_are_preserved(self):
        self._sign_in()
        filtered = {**self.directory, "filters": RecordFilters("Synthetic", "Synthetic East"),
                    "page": 2, "page_count": 3, "total": 60,
                    "range_start": 26, "range_end": 50,
                    "items": self.directory["items"] * 25}
        with patch("routes.households.get_record_page", return_value=filtered) as query:
            response = self.client.get("/households?q=Synthetic&barangay=Synthetic+East&page=2")
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with(2, RecordFilters("Synthetic", "Synthetic East"))
        self.assertIn('value="Synthetic"', response.text)
        self.assertIn('value="Synthetic East" selected', response.text)
        self.assertIn('page=3', response.text)
        self.assertIn('barangay=Synthetic+East', response.text)
        self.assertIn("Showing <strong>26–50</strong> of 60 households", response.text)
        # Clear Filters resets search and barangay; the year is not a request parameter.
        self.assertIn('<a href="/households">Clear Filters</a>', response.text)
        with patch("routes.households.get_record_detail", return_value=self.record):
            detail = self.client.get(
                f"/households/{HOUSEHOLD_UUID}?q=Synthetic&barangay=Synthetic+East&page=2"
            )
        self.assertEqual(detail.status_code, 200)
        self.assertIn('/households?page=2&amp;q=Synthetic&amp;barangay=Synthetic+East', detail.text)
        self.assertIn(f'/assessments/{HOUSEHOLD_UUID}', detail.text)
        self.assertIn("Synthetic answer", detail.text)
        self.assertIn("<td>35</td>", detail.text)
        self.assertIn("These are answers from the 2022 household survey. "
                      "Missing answers do not mean ‘No’.", detail.text)
        # Sections appear in the approved order; the old summary strip and jump links are gone.
        headings = ["Household Composition", "Water &amp; Sanitation", "Household Members"]
        positions = [detail.text.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('class="roster-table"', detail.text)
        self.assertIn("“—” means no answer was recorded", detail.text)
        self.assertNotIn("Age not recorded or unclear", detail.text)
        for removed in ('href="#member-roster"', "record-overview", "Members in recorded roster"):
            self.assertNotIn(removed, detail.text)
        unclear = {**self.record, "composition": {**self.record["composition"],
                                                  "adults": 0, "unclear_age": 1}}
        with patch("routes.households.get_record_detail", return_value=unclear):
            self.assertIn("Age not recorded or unclear",
                          self.client.get(f"/households/{HOUSEHOLD_UUID}").text)
        self._sign_in("system_administrator")
        with patch("routes.households.get_record_detail", return_value=self.record):
            admin_detail = self.client.get(f"/households/{HOUSEHOLD_UUID}")
        self.assertEqual(admin_detail.status_code, 200)
        self.assertIn(f'/assessments/{HOUSEHOLD_UUID}', admin_detail.text)

    def test_invalid_filters_missing_record_and_unavailable_database_fail_safely(self):
        self._sign_in()
        with patch("routes.households.get_record_page", return_value=self.directory) as query:
            self.assertEqual(self.client.get("/households?page=0").status_code, 400)
            self.assertEqual(self.client.get("/households?q=a&q=b").status_code, 400)
            query.assert_not_called()
        with patch("routes.households.get_record_page", return_value=None):
            self.assertEqual(self.client.get("/households?page=9").status_code, 404)
        with patch("routes.households.get_record_page",
                   side_effect=AssessmentQueryError("private_source_detail")):
            response = self.client.get("/households")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private_source_detail", response.text)
        with patch("routes.households.get_record_detail",
                   side_effect=AssessmentQueryError("household_record_missing")):
            self.assertEqual(self.client.get(f"/households/{HOUSEHOLD_UUID}").status_code, 404)
        self.assertEqual(self.client.get("/households/OLD_PRIVATE_ID").status_code, 410)

    def test_synthetic_source_text_is_escaped(self):
        self._sign_in()
        bad = {**self.directory, "items": [{**self.directory["items"][0],
                                            "head_name": "<script>alert(1)</script>"}]}
        with patch("routes.households.get_record_page", return_value=bad):
            response = self.client.get("/households")
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)


class HouseholdRecordFilterTests(unittest.TestCase):
    def test_search_and_barangay_are_normalized_and_duplicates_rejected(self):
        page, filters = parse_record_arguments(MultiDict([
            ("q", "  Synthetic   Head  "), ("barangay", " Synthetic East "), ("page", "2")
        ]))
        self.assertEqual((page, filters), (2, RecordFilters("Synthetic Head", "Synthetic East")))
        for arguments in (MultiDict([("q", "a"), ("q", "b")]),
                          MultiDict([("page", "-1")]),
                          MultiDict([("barangay", "x" * 201)])):
            with self.assertRaises(InvalidRecordFilters):
                parse_record_arguments(arguments)


CONTEXT = {"batch_id": 1, "run_id": 2, "survey_year": 2022, "municipality": "Pozorrubio"}
SYNTHETIC_CODEBOOK = [
    {"variable_name": variable, "code": code, "label": label}
    for variable, code, label in (
        ("r01_building_type", "1", "Single house"),
        ("a02_relation_to_hh_head", "1", "Head"),
        ("a02_relation_to_hh_head", "3", "Son"),
        ("a05_sex", "1", "Male"),
        ("c02_hgc_group", "3", "Elementary Graduate"),
        ("e18_class_of_worker", "3", "Self-employed without any paid employee"),
    )
]


def synthetic_member(line_number, age, **answers):
    return {"line_number": line_number,
            "responses": {"a01_first_name": f"Synthetic{line_number}", "a07_age": age, **answers}}


class HouseholdRecordDetailServiceTests(unittest.TestCase):
    """Detail presentation with mocked read-only queries and synthetic answers."""

    def detail(self, household_answers, members):
        with (
            patch.object(records, "_read_context",
                         return_value=nullcontext((MagicMock(), CONTEXT))),
            patch.object(records.household_records, "find_record", return_value={
                "source_row_number": 1, "barangay": "Synthetic East",
                "responses": household_answers}),
            patch.object(records.dataset_batches, "find_household_identities",
                         return_value=[{"head_count": 1, "first_name": "Synthetic",
                                        "middle_name": "", "last_name": "Head"}]),
            patch.object(records.household_records, "list_record_members", return_value=members),
            patch.object(records.assessment_evidence, "list_evidence_references", return_value={
                "codebook": SYNTHETIC_CODEBOOK, "definitions": [], "mpi_mapping": []},
            ) as references,
        ):
            result = records.get_record_detail(str(HOUSEHOLD_UUID))
        # Codebook labels are requested for the fields this page shows, including new ones.
        requested = references.call_args.args[2]
        for field in ("r01_building_type", "q23_handwashing", "e18_class_of_worker"):
            self.assertIn(field, requested)
        return result

    def test_survey_age_uses_whole_numbers_from_0_to_120(self):
        for raw, expected in (("0", 0), ("17", 17), ("18", 18), ("60", 60), ("120", 120),
                              ("35.0", 35), ("121", None), ("-1", None), ("12.5", None),
                              ("abc", None), ("NA", None), ("", None), (None, None)):
            with self.subTest(raw=raw):
                self.assertEqual(records.survey_age(raw), expected)

    def test_composition_groups_always_add_up_to_total(self):
        ages = ("5", "17", "18", "59", "60", "120", "NA", "", "12.5", "abc", "130", None)
        composition = records.count_household_composition(
            [synthetic_member(number, age) for number, age in enumerate(ages, 1)])
        self.assertEqual(composition, {"total": 12, "children": 2, "adults": 2,
                                       "older_adults": 2, "unclear_age": 6})
        self.assertEqual(composition["children"] + composition["adults"]
                         + composition["older_adults"] + composition["unclear_age"],
                         composition["total"])
        self.assertEqual(records.count_household_composition([])["total"], 0)

    def test_household_answers_decode_codes_and_keep_uncertainty_visible(self):
        cases = (
            ({"r01_building_type": "1"}, "Building type", "Single house"),
            ({"r01_building_type": "99"}, "Building type", "Meaning unclear"),
            ({"r01_building_type": "NA"}, "Building type", "Not recorded"),
            ({"r09_number_of_bedrooms": "0"}, "Bedrooms", "0"),
            ({"r09_number_of_bedrooms": "25"}, "Bedrooms", "25"),
            ({"r09_number_of_bedrooms": ""}, "Bedrooms", "Not recorded"),
            ({"r09_number_of_bedrooms": "2.5"}, "Bedrooms", "Meaning unclear"),
            ({"r09_number_of_bedrooms": "-1"}, "Bedrooms", "Meaning unclear"),
            ({"r09_number_of_bedrooms": "several"}, "Bedrooms", "Meaning unclear"),
        )
        for answers, label, expected in cases:
            with self.subTest(answers=answers):
                result = self.detail(answers, [])
                cells = {cell["label"]: cell["value"]
                         for group in result["groups"] for cell in group["cells"]}
                self.assertEqual(cells[label], expected)
        self.assertEqual([group["title"] for group in result["groups"]],
                         ["Housing & Utilities", "Water & Sanitation"])

    def test_roster_shows_dash_only_for_missing_answers(self):
        members = [
            synthetic_member(1, "45", a02_relation_to_hh_head="1", a05_sex="1",
                             c02_hgc_group="3", e18_class_of_worker="3", e01_employed=""),
            synthetic_member(2, "130", a02_relation_to_hh_head="3", e18_class_of_worker="77",
                             c03_currently_attending_school="NA"),
        ]
        result = self.detail({}, members)
        columns = result["member_columns"]
        first, second = (dict(zip(columns, member["answers"])) for member in result["members"])
        self.assertEqual(first["Relationship to head"], "Head")
        self.assertEqual(first["Sex"], "Male")
        self.assertEqual(first["Age in 2022"], "45")
        self.assertEqual(first["Highest grade completed"], "Elementary Graduate")
        self.assertEqual(first["Class of worker"], "Self-employed without any paid employee")
        self.assertEqual(first["Worked in the past week"], "—")
        self.assertEqual(second["Age in 2022"], "Age unclear")
        self.assertEqual(second["Class of worker"], "Meaning unclear")
        self.assertEqual(second["Attending school"], "—")
        self.assertEqual(second["Sex"], "—")
        self.assertEqual(result["composition"]["unclear_age"], 1)
        self.assertEqual(len(columns), 7)
        # Household Records shows recorded answers only, never assessment results.
        for finding_word in ("Deprived", "For Review", "Priority", "rank"):
            self.assertNotIn(finding_word, str(result))


if __name__ == "__main__":
    unittest.main()
