"""Synthetic tests for protected detail navigation, stored outcomes and evidence."""

from copy import deepcopy
from html import unescape
import logging
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID, detail_source
from services import assessment_details as details
from services.assessment_queries import AssessmentQueryError
from services.assessment_reference_labels import evidence_cell, reference_index
from services.assessment_evidence_view import direct_condition, prepare_evidence_view
from services.mpi_evidence import build_evidence_index, build_indicator_evidence
from services.vulnerability.scoring import DIMENSIONS, INDICATOR_WEIGHTS
from services.assessment_request_logging import AssessmentRequestFilter


class AssessmentDetailPresentationTests(unittest.TestCase):
    def setUp(self):
        self.source = detail_source()

    def test_original_flags_exact_weights_metrics_and_source_are_preserved(self):
        before = deepcopy(self.source)
        result = details.prepare_assessment_detail(self.source)
        self.assertEqual(self.source, before)
        self.assertEqual(result["mpi"], self.source["findings"]["mpi"])
        self.assertEqual(result["household_reference_id"], "HH-00008")
        self.assertEqual(result["determined_count"], 15)
        self.assertEqual([dimension["name"] for dimension in result["dimensions"]], list(DIMENSIONS))
        self.assertEqual([dimension["points"] for dimension in result["dimensions"][:2]], [12.5, 12.5])
        self.assertAlmostEqual(sum(dimension["points"] for dimension in result["dimensions"]), 36.11111111111111)
        indicators = [indicator for dimension in result["dimensions"] for indicator in dimension["indicators"]]
        self.assertEqual(len(indicators), 16)
        for indicator in indicators:
            self.assertEqual(indicator["flag"], self.source["findings"]["mpi"][indicator["name"]])
        internet = next(indicator for indicator in indicators if indicator["name"] == "Internet")
        self.assertEqual((internet["status"], internet["contribution"], internet["weight"]),
                         ("Unknown", 0.0, "1/36"))
        self.source["findings"]["mpi"]["vulnerability_score"] = 40
        with self.assertRaisesRegex(AssessmentQueryError, "stored_score_contribution_mismatch"):
            details.prepare_assessment_detail(self.source)

    def test_relevant_members_and_unknown_age_are_not_silently_omitted(self):
        index = build_evidence_index(self.source)
        attendance = build_indicator_evidence("School Attendance", self.source, index)
        self.assertEqual([member["line_number"] for member in attendance["members"]], [2])
        employment = build_indicator_evidence("Employment Proxy", self.source, index)
        self.assertEqual([member["line_number"] for member in employment["members"]], [1])
        self.source["members"][2]["responses"]["a07_age"] = "NA"
        self.source["members"][2]["responses"]["a01_first_name"] = ""
        self.source["members"][2]["responses"]["a01_last_name"] = "NA"
        attendance = build_indicator_evidence("School Attendance", self.source, index)
        self.assertEqual([member["line_number"] for member in attendance["members"]], [2, 3])
        self.assertEqual(attendance["members"][1]["name"], "Member line 3")
        crowding = build_indicator_evidence("Overcrowding", self.source, index)
        self.assertEqual(crowding["roster_count"], 3)
        self.assertEqual(crowding["members"], [])

    def test_illness_skip_missing_join_and_provisional_source_meaning(self):
        index = build_evidence_index(self.source)
        skipped = build_indicator_evidence("Illness/Treatment Proxy", self.source, index)
        self.assertEqual(skipped["members"], [])
        self.source["household"]["l33_member_who_has_been_sick"] = "1"
        self.source["members"][0]["health_present"] = False
        self.source["members"][0]["health_responses"] = {}
        evidence = build_indicator_evidence("Illness/Treatment Proxy", self.source, index)
        self.assertTrue(evidence["members"][0]["health_missing"])
        self.assertIn("provisional", evidence["limitations"][0])
        cell = evidence_cell("l34_has_been_sick", "1", "section_l02_to_l41", index)
        self.assertIn("provisional", cell["meaning"])
        self.assertTrue(any("PSA" in note for note in cell["notes"]))

    def test_actual_source_assumptions_and_conflicts_are_labeled_without_new_flags(self):
        index = build_evidence_index(self.source)
        member = self.source["members"][0]
        member["responses"].update(b06_ofi="1", e01_employed="NA")
        before = deepcopy(self.source["findings"])
        employment = build_indicator_evidence("Employment Proxy", self.source, index)
        self.assertTrue(any("assumed-working" in note for note in employment["members"][0]["notes"]))
        underemployment = build_indicator_evidence("Underemployment Proxy", self.source, index)
        self.assertFalse(any("assumed-working" in note for note in underemployment["members"][0]["notes"]))
        member["responses"]["e01_employed"] = "1"
        employment = build_indicator_evidence("Employment Proxy", self.source, index)
        self.assertTrue(any("routing conflict" in note for note in employment["members"][0]["notes"]))
        self.source["household"]["l33_member_who_has_been_sick"] = "1"
        member["health_responses"].update(l34_has_been_sick="2", l38_availed_medical_treatment="1")
        illness = build_indicator_evidence("Illness/Treatment Proxy", self.source, index)
        self.assertTrue(any("conflicting follow-up" in note for note in illness["members"][0]["notes"]))
        self.assertEqual(self.source["findings"], before)

    def test_decoding_missing_unknown_conflicting_codes_and_reference_provenance(self):
        index = build_evidence_index(self.source)
        cell = evidence_cell("c03_currently_attending_school", "2", "section_a_e_indiv", index)
        self.assertEqual(cell["meaning"], "No")
        self.assertEqual(cell["raw"], "2")
        unknown = evidence_cell("c03_currently_attending_school", "77", "section_a_e_indiv", index)
        self.assertIn("No verified code meaning", unknown["meaning"])
        missing = evidence_cell("c03_currently_attending_school", "NA", "section_a_e_indiv", index)
        self.assertEqual(missing["raw"], "NA")
        self.assertIn("missing", missing["meaning"])
        refs = {"codebook": [
            {"variable_name": "test", "code": "1", "label": "Yes"},
            {"variable_name": "test", "code": "1", "label": "No"},
        ]}
        conflict = evidence_cell("test", "1", "household", reference_index(refs))
        self.assertIn("conflict", conflict["meaning"])
        e15 = evidence_cell("e15_want_more_hours", "1", "section_a_e_indiv", index)
        self.assertTrue(any("Synthetic Support" in note for note in e15["notes"]))

    def test_staff_evidence_groups_preserve_source_and_label_uncertainty(self):
        index = build_evidence_index(self.source)
        food = prepare_evidence_view(
            "Food Insecurity", build_indicator_evidence("Food Insecurity", self.source, index),
        )
        self.assertEqual(len(food["groups"][0]["cells"]), 8)
        self.assertEqual(food["groups"][0]["layout"], "grid")
        self.assertEqual(food["groups"][0]["cells"][0]["value"], "Yes")
        assets = prepare_evidence_view(
            "Assets", build_indicator_evidence("Assets", self.source, index),
        )
        self.assertEqual([group["title"] for group in assets["groups"]], [
            "Communication", "Durable household items", "Transportation",
        ])
        self.assertEqual(sum(len(group["cells"]) for group in assets["groups"]), 15)
        self.assertEqual(
            next(cell for cell in assets["groups"][0]["cells"]
                 if cell["source"]["variable"] == "r16_n_mobile_phone")["value"],
            "1 recorded",
        )
        internet = prepare_evidence_view(
            "Internet", build_indicator_evidence("Internet", self.source, index),
        )
        self.assertEqual(internet["groups"][0]["cells"][0]["value"], "Not recorded")
        self.assertIsNone(direct_condition("Internet", None, internet))
        self.assertEqual(internet["household_cells"][0]["raw"], "NA")

    def test_staff_member_and_space_views_do_not_assign_new_outcomes(self):
        index = build_evidence_index(self.source)
        before = deepcopy(self.source["findings"])
        school = prepare_evidence_view(
            "School Attendance", build_indicator_evidence("School Attendance", self.source, index),
        )
        self.assertEqual(school["members"][0]["name"], "Synthetic Member 2")
        self.assertEqual(school["members"][0]["display_cells"][0]["value"], "No")
        self.assertEqual(school["members"][0]["cells"][1]["raw"], "2")
        employment = prepare_evidence_view(
            "Employment Proxy", build_indicator_evidence("Employment Proxy", self.source, index),
        )
        self.assertEqual([member["line_number"] for member in employment["members"]], [1])
        self.assertEqual(employment["members"][0]["display_cells"][0]["value"],
                         "Resident in the Philippines")
        crowding = prepare_evidence_view(
            "Overcrowding", build_indicator_evidence("Overcrowding", self.source, index),
        )
        self.assertIn("16.67 m² per person", crowding["space_comparison"])
        self.assertNotIn("benchmark", crowding["space_comparison"])
        self.assertEqual(self.source["findings"], before)

    def test_priority_indicators_present_all_statuses_without_reassessing_answers(self):
        before = deepcopy(self.source)
        reference = build_evidence_index(self.source)
        for name in ("Food Insecurity", "School Attendance", "Educational Attainment",
                     "Employment Proxy", "Illness/Treatment Proxy"):
            for flag, status in ((0, "Not Deprived"), (1, "Deprived"), (None, "Unknown")):
                with self.subTest(name=name, flag=flag):
                    stored = {**self.source["findings"]["mpi"], name: flag}
                    indicator = details._indicator(name, stored, self.source, reference)
                    self.assertEqual(indicator["status"], status)
                    self.assertEqual(indicator["flag"], flag)
                    self.assertTrue(indicator["evidence"]["context"])
                    self.assertTrue(indicator["explanation"])
        self.assertEqual(self.source, before)

    def test_illness_display_distinguishes_both_codes_and_keeps_uncertainty(self):
        self.source["household"]["l33_member_who_has_been_sick"] = "1"
        cases = (
            ("1", "Affected by illness"),
            ("1.0", "Affected by illness"),
            ("2", "Not affected by illness"),
            ("2.0", "Not affected by illness"),
            ("77", "Meaning unclear"),
            ("NA", "Not recorded"),
            ("", "Not recorded"),
            (None, "Not recorded"),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.source["members"][0]["health_responses"]["l34_has_been_sick"] = raw
                before = deepcopy(self.source)
                evidence = build_indicator_evidence(
                    "Illness/Treatment Proxy", self.source, build_evidence_index(self.source),
                )
                original = deepcopy(evidence)
                view = prepare_evidence_view("Illness/Treatment Proxy", evidence)
                member = view["members"][0]
                cell = next(cell for cell in member["display_cells"]
                            if cell["source"]["variable"] == "l34_has_been_sick")
                record = next(cell for cell in member["record_cells"]
                              if cell["variable"] == "l34_has_been_sick")
                self.assertEqual(cell["value"], expected)
                self.assertEqual(record["meaning"], expected)
                self.assertEqual(record["raw"], cell["source"]["raw"])
                self.assertEqual(evidence, original)
                self.assertEqual(self.source, before)

    def test_all_record_projections_keep_original_values_and_internal_metadata(self):
        self.source["household"]["l33_member_who_has_been_sick"] = "1"
        self.source["members"][0]["responses"].update(b06_ofi="1", e01_employed="NA")
        index = build_evidence_index(self.source)
        for name in INDICATOR_WEIGHTS:
            with self.subTest(indicator=name):
                evidence = build_indicator_evidence(name, self.source, index)
                before = deepcopy(evidence)
                view = prepare_evidence_view(name, evidence)
                pairs = [(evidence["household_cells"], view["record_cells"])]
                pairs.extend((original["cells"], member["record_cells"])
                             for original, member in zip(evidence["members"], view["members"]))
                for original_cells, records in pairs:
                    self.assertEqual(len(original_cells), len(records))
                    for original, record in zip(original_cells, records):
                        self.assertEqual(set(record), {"label", "variable", "raw", "meaning"})
                        self.assertEqual(record["variable"], original["variable"])
                        self.assertEqual(record["raw"], original["raw"])
                self.assertEqual(evidence, before)
                self.assertEqual(view["limitations"], evidence["limitations"])
                self.assertEqual(view["household_cells"], evidence["household_cells"])
                for original, member in zip(evidence["members"], view["members"]):
                    self.assertEqual(member["cells"], original["cells"])
                    self.assertEqual(member["notes"], original["notes"])

    def test_all_indicator_outcome_wording_and_missing_roster_reference_fallbacks(self):
        for flag in (0, 1, None):
            source = detail_source()
            mpi = source["findings"]["mpi"]
            for name in INDICATOR_WEIGHTS:
                mpi[name] = flag
            mpi["vulnerability_score"] = 100 if flag == 1 else 0
            mpi["unknown_indicator_count"] = 16 if flag is None else 0
            mpi["deprived_indicator_count"] = 16 if flag == 1 else 0
            mpi["not_deprived_indicator_count"] = 16 if flag == 0 else 0
            mpi["coverage_pct"] = 0 if flag is None else 100
            source["members"] = []
            source["references"] = {}
            result = details.prepare_assessment_detail(source)
            for dimension in result["dimensions"]:
                for indicator in dimension["indicators"]:
                    self.assertEqual(indicator["flag"], flag)
                    self.assertTrue(indicator["explanation"])
                    self.assertTrue(indicator["rule"])
                    self.assertEqual(indicator["evidence"]["members"], [])
        cell = evidence_cell("q01_main_water", "3", "household", reference_index({}))
        self.assertIn("No verified code meaning", cell["meaning"])

    def test_access_logs_mask_uuid_search_and_filters_but_retain_status(self):
        log_filter = AssessmentRequestFilter()
        record = logging.LogRecord(
            "werkzeug", logging.INFO, "synthetic", 1, '%s "%s" %s',
            ("127.0.0.1", f"GET /assessments/{HOUSEHOLD_UUID}?q=PRIVATE_NAME HTTP/1.1", "200"), None,
        )
        log_filter.filter(record)
        self.assertNotIn(HOUSEHOLD_UUID, record.getMessage())
        self.assertNotIn("PRIVATE_NAME", record.getMessage())
        self.assertIn("200", record.getMessage())
        self.assertIn("/assessments/[protected]", record.getMessage())


class AssessmentDetailRouteTests(unittest.TestCase):
    def setUp(self):
        self.source = detail_source()
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None})
        self.client = self.app.test_client()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        patches = (
            patch("psycopg.connect", side_effect=AssertionError("No real database connections")),
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
            patch.object(details, "get_active_assessment_detail", return_value=self.source),
        )
        for patcher in patches:
            mock = patcher.start()
            self.addCleanup(patcher.stop)
        self.query = mock
        self.path = f"/assessments/{HOUSEHOLD_UUID}"

    def _session(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()), csrf_token="synthetic-csrf")

    def test_anonymous_inactive_revoked_expired_and_unknown_roles_cannot_read_evidence(self):
        self.assertEqual(self.client.get(self.path + "?tab=mpi").location, "/login")
        for field, value in (("is_active", False), ("session_version", 2), ("role", "unapproved")):
            with self.subTest(field=field), patch.dict(self.user, {field: value}):
                self._session()
                self.assertEqual(self.client.get(self.path).location, "/login")
        self._session()
        with self.client.session_transaction() as session:
            session["issued_at"] = 0
        self.assertEqual(self.client.get(self.path).location, "/login")
        self.query.assert_not_called()

    def test_both_roles_read_stored_overview_and_sixteen_collapsible_indicators(self):
        for role in ("lgu_staff", "system_administrator"):
            with self.subTest(role=role), patch.dict(self.user, role=role):
                self._session()
                overview = self.client.get(self.path)
                self.assertEqual(overview.status_code, 200)
                self.assertEqual(overview.headers["Cache-Control"], "no-store")
                for text in ("HH-00008", "Synthetic Member 1", "36.11", HOUSEHOLD_UUID,
                             "Municipal Vulnerability Rank", "Needs Data Review",
                             "No Review Signal", "15", "understate its needs"):
                    self.assertIn(text, overview.text)
                for removed in ("Assessment Run 2", "How it works", "data coverage",
                                "Assessment information", "No Historical CBMS Trigger"):
                    self.assertNotIn(removed, overview.text)
                mpi = self.client.get(self.path + "?tab=mpi")
                self.assertEqual(mpi.status_code, 200)
                self.assertEqual(mpi.text.count('class="mpi-indicator"'), 16)
                self.assertEqual(mpi.text.count('class="cbms-evidence"'), 16)
                dimensions = re.findall(r'<details class="mpi-dimension"[^>]*>', mpi.text)
                self.assertEqual(len(dimensions), 4)
                self.assertTrue(all("open" not in dimension for dimension in dimensions))
                self.assertNotIn('<details class="cbms-evidence" open', mpi.text)
                self.assertIn("Synthetic Member 2", mpi.text)
                self.assertNotIn("e15_want_more_hours", mpi.text)
                self.assertNotIn("Synthetic Support", mpi.text)
                self.assertIn("View Evidence", mpi.text)
                self.assertNotIn("CBMS Record Details", mpi.text)
                self.assertNotIn('class="cbms-record-details"', mpi.text)
                self.assertIn("Synthetic Member 2 (age 12) was recorded as not attending school", mpi.text)
                self.assertNotIn("16 stored outcomes", mpi.text)
                self.assertNotIn("<small>Weight</small>", mpi.text)
                self.assertNotIn("Child Mortality", mpi.text)
                self.assertNotIn("Access to Health Facilities", mpi.text)

    def test_overview_summary_uses_stored_results_and_visible_explanations(self):
        self._session()
        before = deepcopy(self.source)
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        summary = re.search(r'<dl class="assessment-metrics".*?</dl>', response.text, re.S)[0]
        self.assertEqual(summary.count("<dt>"), 3)
        for text in ("36.11", "/ 100", "7", "of 51", "15", "of 16 determined",
                     "1 unknown result.", "Higher scores indicate greater assessed household vulnerability.",
                     "Households with equal scores share the same rank."):
            self.assertIn(text, summary)
        self.assertIn("Indicator with an unknown result", response.text)
        self.assertIn("#mpi-internet", response.text)
        for text in ("Areas Contributing to Vulnerability", "Each can contribute up to 25 points.",
                     "Overall Vulnerability Score", "This does not mean the household is ineligible.",
                     "Missing or conflicting information prevents determining a screening match."):
            self.assertIn(text, response.text)
        bars = re.findall(r'<meter.*?value="([^"]+)".*?</meter>', response.text, re.S)
        expected = details.prepare_assessment_detail(self.source)["dimensions"]
        self.assertEqual([float(value) for value in bars], [item["points"] for item in expected])
        screening = re.search(r'<dl class="screening-summary-list">.*?</dl>',
                              response.text, re.S)[0]
        findings = re.findall(r'<dt>(.*?)</dt>\s*<dd\b[^>]*>\s*(.*?)\s*</dd>',
                              screening, re.S)
        self.assertEqual(findings, [
            ("4Ps", "For Review"), ("SLP", "Needs Data Review"),
            ("SPISC", "No Review Signal"), ("TUPAD", "For Review"),
        ])
        self.assertEqual(screening.count('class="screening-status screening-status--review"'), 2)
        self.assertIn('class="screening-status screening-status--data"', screening)
        self.assertIn('class="screening-status"', screening)
        self.assertEqual(self.source, before)
        for program in self.source["findings"]["programs"].values():
            program["screening_status"] = "For Review"
        response = self.client.get(self.path)
        guide = re.search(r'<dl class="screening-status-guide".*?</dl>', response.text, re.S)[0]
        self.assertEqual(re.findall(r'<dt>(.*?)</dt>', guide), ["For Review"])

    def test_complete_and_multiple_unknown_results_have_accurate_descriptions(self):
        self._session()
        mpi = self.source["findings"]["mpi"]
        mpi.update({"Internet": 0, "unknown_indicator_count": 0,
                    "unknown_zero_point_indicators": (), "coverage_pct": 100})
        response = self.client.get(self.path)
        self.assertIn("16 <small>of 16 determined</small>", response.text)
        self.assertIn("0 unknown results.", response.text)
        self.assertNotIn('id="unknown-heading"', response.text)
        mpi.update({"Internet": None, "Working Child Not in School": None,
                    "unknown_indicator_count": 2,
                    "unknown_zero_point_indicators": ("Internet", "Working Child Not in School")})
        response = self.client.get(self.path)
        self.assertIn("14 <small>of 16 determined</small>", response.text)
        self.assertIn("2 unknown results.", response.text)
        self.assertIn("Indicators with unknown results", response.text)
        self.assertIn("#mpi-internet", response.text)
        self.assertIn("#mpi-working-child-not-in-school", response.text)
        self.assertIn("whether these conditions apply", response.text)
        self.assertNotIn("all answers", response.text.lower())

    def test_identity_survey_year_and_unknown_navigation_use_existing_context(self):
        self._session()
        self.source["context"].update(survey_year=2025, household_count=19304)
        self.source["identity"].update(first_name="Synthetic", middle_name="Recorded",
                                      last_name="Full Name")
        response = self.client.get(self.path + "?page=2&completeness=unknown")
        self.assertIn("Historical CBMS 2025", response.text)
        self.assertNotIn("Historical CBMS 2022", response.text)
        self.assertIn("Synthetic Recorded Full Name", response.text)
        self.assertIn(f'<dd class="detail-record-id">{HOUSEHOLD_UUID}</dd>', response.text)
        self.assertIn("<dt>Barangay</dt>", response.text)
        self.assertIn("<dd>Synthetic Barangay</dd>", response.text)
        self.assertNotIn("Synthetic Barangay · Pozorrubio", response.text)
        self.assertIn("of 19,304", response.text)
        link = re.search(r'href="([^"]+)"\s*>Internet',
                         response.text)[1]
        url = urlsplit(unescape(link))
        self.assertEqual(url.path, self.path)
        self.assertEqual(url.fragment, "mpi-internet")
        self.assertEqual(parse_qs(url.query), {
            "tab": ["mpi"], "page": ["2"], "completeness": ["unknown"],
        })
        destination = self.client.get(url.path + "?" + url.query)
        self.assertEqual(destination.status_code, 200)
        self.assertIn('id="mpi-heading"', destination.text)
        self.assertEqual(destination.text.count('class="cbms-evidence"'), 16)

    def test_both_tabs_share_summary_and_only_unknown_households_have_mpi_notice(self):
        self._session()
        for unknown_count in (1, 0):
            if not unknown_count:
                self.source["findings"]["mpi"].update({
                    "Internet": 0, "unknown_indicator_count": 0,
                    "unknown_zero_point_indicators": (),
                })
            overview = self.client.get(self.path)
            mpi = self.client.get(self.path + "?tab=mpi")
            summaries = [re.search(r'<dl class="assessment-metrics".*?</dl>',
                                   page.text, re.S)[0] for page in (overview, mpi)]
            self.assertEqual(summaries[0], summaries[1])
            self.assertEqual(summaries[0].count("<dt>"), 3)
            self.assertEqual('id="mpi-unknown-heading"' in mpi.text, bool(unknown_count))
            if unknown_count:
                self.assertIn("1 indicator could not be determined", " ".join(mpi.text.split()))
                self.assertIn("View under Housing, Water and Sanitation", mpi.text)

    def test_all_indicators_have_distinct_stable_links_and_actual_dimension_context(self):
        self._session()
        mpi = self.source["findings"]["mpi"]
        mpi.update(dict.fromkeys(INDICATOR_WEIGHTS, None))
        mpi.update(vulnerability_score=0, unknown_indicator_count=16,
                   unknown_zero_point_indicators=tuple(INDICATOR_WEIGHTS))
        before = deepcopy(self.source)
        prepared = details.prepare_assessment_detail(self.source)
        unknowns = prepared["unknown_indicators"]
        self.assertEqual(len(unknowns), 16)
        self.assertEqual(len({item["id"] for item in unknowns}), 16)
        self.assertEqual(set(details.INDICATOR_IDS), set(INDICATOR_WEIGHTS))
        overview = self.client.get(self.path + "?page=2&program=4Ps")
        destination = self.client.get(self.path + "?tab=mpi&page=2&program=4Ps")
        for item in unknowns:
            with self.subTest(indicator=item["name"]):
                self.assertRegex(item["id"], r"^mpi-[a-z-]+$")
                self.assertIn(f'#{item["id"]}', overview.text)
                self.assertIn(f'href="#{item["id"]}"', destination.text)
                self.assertIn(f'id="{item["id"]}"', destination.text)
                self.assertIn(f'View under {item["dimension"]}', destination.text)
                self.assertIn(item["name"], DIMENSIONS[item["dimension"]][1])
        self.assertNotIn('<details class="mpi-dimension" open', destination.text)
        self.assertNotIn('<details class="cbms-evidence" open', destination.text)
        self.assertEqual(self.source, before)

    def test_operational_html_excludes_methodology_but_retains_answers_and_conflicts(self):
        self._session()
        self.source["household"].update({
            "l33_member_who_has_been_sick": "1", "n01_internet_access": "1",
            "h01_access_to_public_transportation": "2", "r16_s_jeep": "1",
        })
        adult = self.source["members"][0]
        adult["responses"].update(b06_ofi="1", e01_employed="NA",
                                  e15_want_more_hours="1", c02_hgc_group="5")
        adult["health_responses"].update(l34_has_been_sick="1",
                                        l38_availed_medical_treatment="2")
        self.source["members"][1]["health_responses"].update(
            l34_has_been_sick="2", l38_availed_medical_treatment="1",
        )
        self.source["findings"]["mpi"]["assumptions_used"] = ("INTERNAL_ASSUMPTION_MARKER",)
        before = deepcopy(self.source)
        overview = self.client.get(self.path)
        mpi = self.client.get(self.path + "?tab=mpi")
        for response in (overview, mpi):
            self.assertEqual(response.status_code, 200)
            for text in (
                "Synthetic Support", "pilot", "provenance", "provisional", "assumed",
                "assumptions", "limitations", "Assessment rule", "Indicator weight",
                "questionnaire", "routing", "benchmark", "Approved response mapping",
                "INTERNAL_ASSUMPTION_MARKER", "not wholly observed", "PSA",
            ):
                with self.subTest(text=text):
                    self.assertNotIn(text.lower(), response.text.lower())
        self.assertIn("Affected by illness", mpi.text)
        self.assertIn("Not affected by illness", mpi.text)
        self.assertNotIn("meaning unconfirmed", mpi.text)
        self.assertIn("Recorded answers conflict; they do not establish a clear response.", mpi.text)
        self.assertIn("Not recorded", mpi.text)
        self.assertNotIn("Recorded Value:", mpi.text)
        articles = re.findall(r'<article class="mpi-indicator".*?</article>', mpi.text, re.S)
        self.assertEqual(len(articles), 16)
        for article in articles:
            positions = [article.index(marker) for marker in (
                "<h3>", 'class="indicator-status', 'class="indicator-explanation"',
                'class="indicator-number"', 'class="cbms-evidence"',
            )]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn("CBMS Record Details", article)
            self.assertNotIn("section_a_e_indiv", article)
            self.assertNotIn("record-details-body", article)
        for name, expected in (("Internet", "Yes"), ("Public Transportation", "No")):
            article = next(article for article in articles if f"<h3>{name}</h3>" in article)
            self.assertIn(f"<dd>{expected}</dd>", article)
        underemployment = next(article for article in articles
                               if "<h3>Wanting More Work Hours</h3>" in article)
        self.assertIn("Wanted more work hours in the past week</dt>", underemployment)
        self.assertIn("<dd>Yes</dd>", underemployment)
        self.assertEqual(self.source, before)

    def test_mpi_context_and_display_names_preserve_answers_and_historical_period(self):
        self._session()
        before = deepcopy(self.source)
        page = self.client.get(self.path + "?tab=mpi").text
        food = re.search(r'<article[^>]+id="mpi-food-insecurity".*?</article>', page, re.S)[0]
        for wording in ("money or other resources for food were insufficient",
                        "1 July 2021–30 June 2022", "Had to skip a meal"):
            self.assertIn(wording, food)
        self.assertEqual(food.count("<dd>Yes</dd>"), 4)
        self.assertEqual(food.count("<dd>No</dd>"), 4)
        for wording in ("Illness and Treatment", "month before the survey",
                        "Employment</h3>", "Worked at least one hour in the past week",
                        "members aged 5–17", "members aged 19 and older",
                        "Available information cannot establish the result.",
                        "This does not establish an absence of deprivation."):
            self.assertIn(wording, page)
        self.assertNotIn("Proxy", page)
        self.assertNotIn("· age at survey", page)
        self.assertIn("Age at survey:", page)
        self.assertEqual(self.source, before)
        self.source["context"]["survey_year"] = 2025
        self.assertNotIn("1 July 2021–30 June 2022", self.client.get(self.path + "?tab=mpi").text)

    def test_back_links_and_sections_preserve_only_validated_internal_list_state(self):
        self._session()
        query = (
            "page=2&q=Synthetic&barangay=Synthetic+Barangay&program=4Ps"
            "&subsidy_mode=exact&completeness=unknown"
        )
        response = self.client.get(self.path + "?" + query + "&next=https://example.invalid")
        self.assertEqual(response.status_code, 200)
        for href in re.findall(r'href="([^"]+)"', response.text):
            url = urlsplit(unescape(href))
            if url.path == self.path or (url.path == "/assessments" and url.query):
                params = parse_qs(url.query)
                self.assertEqual(params["page"], ["2"])
                self.assertEqual(params["subsidy_mode"], ["exact"])
                self.assertEqual(params["q"], ["Synthetic"])
                self.assertEqual(params["completeness"], ["unknown"])
                self.assertNotIn("next", params)
        self.assertNotIn("https://example.invalid", response.text)

    def test_invalid_and_missing_references_and_query_errors_are_sanitized(self):
        self._session()
        for query in ("tab=other", "tab=mpi&tab=overview", "page=0", "page=2&page=3",
                      "program=other", "subsidy_mode=invalid", "q=%00"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.path + "?" + query).status_code, 400)
        self.assertEqual(self.client.get("/assessments/invalid-id").status_code, 410)
        self.query.assert_not_called()
        self.query.side_effect = AssessmentQueryError("household_assessment_missing")
        self.assertEqual(self.client.get(self.path).status_code, 404)
        self.query.side_effect = AssessmentQueryError("PRIVATE_ERROR_VALUE")
        error = self.client.get(self.path)
        self.assertEqual(error.status_code, 503)
        self.assertNotIn("PRIVATE_ERROR_VALUE", error.text)

    def test_member_source_text_is_escaped_and_unrelated_values_are_not_rendered(self):
        self._session()
        self.source["members"][1]["responses"]["a01_first_name"] = "<script>alert(1)</script>"
        self.source["household"]["unrelated"] = "UNRELATED_PRIVATE_MARKER"
        self.source["members"][1]["responses"]["unrelated"] = "UNRELATED_PRIVATE_MARKER"
        response = self.client.get(self.path + "?tab=mpi")
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>alert", response.text)
        self.assertNotIn("UNRELATED_PRIVATE_MARKER", response.text)
