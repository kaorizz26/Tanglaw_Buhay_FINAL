"""Stored-result presentation, uncertainty and protected screening navigation."""

from copy import deepcopy
from html import unescape
import re
from secrets import token_urlsafe
from time import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from app import create_app
from assessment_detail_fixtures import HOUSEHOLD_UUID, screening_source
from services import assessment_details
from services.mpi_evidence import build_evidence_index
from services.screening.metadata import PROGRAMS, STATUSES
from services.subsidy_screening_content import (
    REASON_TEXT, SLP_UNRESOLVED_NOTE, STATUS_LABELS, WARNING_TEXT,
)
from services.subsidy_screening_details import prepare_subsidy_screening


class ScreeningPresentationTests(unittest.TestCase):
    def setUp(self):
        self.source = screening_source()

    def present(self):
        return prepare_subsidy_screening(self.source, build_evidence_index(self.source))

    def test_stored_findings_and_sources_are_unchanged_and_all_statuses_retained(self):
        for status in STATUSES:
            with self.subTest(status=status):
                for finding in self.source["findings"]["programs"].values():
                    finding["screening_status"] = status
                before = deepcopy(self.source)
                programs = self.present()
                self.assertEqual(self.source, before)
                self.assertEqual([p["code"] for p in programs], list(PROGRAMS))
                self.assertTrue(all(p["status"] == status for p in programs))
                self.assertTrue(all(p["status_label"] == STATUS_LABELS[status]
                                    for p in programs))
                self.assertTrue(all("requirements" not in p for p in programs))
                if status != "For Review":
                    self.assertTrue(all(not p["reasons"] for p in programs))

    def test_every_stored_evidence_kind_has_plain_wording_and_context_is_separate(self):
        for kind, text in REASON_TEXT.items():
            with self.subTest(kind=kind):
                code = "SLP" if kind in ("EF", "MD") else "TUPAD"
                if kind in ("child_age", "household_pregnancy", "member_pregnancy"):
                    code = "4Ps"
                elif kind in ("senior_age", "household_disability", "senior_disability"):
                    code = "SPISC"
                item = {"member_line_number": 1, "observed_values": {"a07_age": "45"}}
                if kind not in ("child_age", "senior_age"):
                    item["pathway" if kind in ("EF", "MD") else "trigger_code"] = kind
                contextual = kind in ("assumed_overseas_work", "household_disability", "senior_disability")
                if contextual:
                    item["evidence_role"] = "supporting_context"
                self.source["findings"]["programs"][code]["supporting_evidence"] = (item,)
                program = next(p for p in self.present() if p["code"] == code)
                destination = program["supporting_context"] if contextual else program["reasons"]
                self.assertEqual(destination[0]["text"], text)
                self.assertEqual(bool(program["reasons"]), not contextual)

    def test_decodes_batch_answers_and_retains_assumed_work_and_unconfirmed_health(self):
        self.source["members"][2]["health_responses"]["l16_pwd"] = "1"
        self.source["findings"]["programs"]["SPISC"]["supporting_evidence"] += ({
            "member_line_number": 3, "trigger_code": "senior_disability",
            "evidence_role": "supporting_context",
            "observed_values": {"a07_age": "80", "l16_pwd": "1"},
        },)
        programs = {p["code"]: p for p in self.present()}
        tupad = programs["TUPAD"]
        self.assertIn("not a recorded No answer", tupad["reasons"][0]["text"])
        values = {cell["label"]: cell["value"] for cell in tupad["members"][0]["cells"]}
        self.assertEqual(
            values["Worked at least one hour during the survey reference week"],
            "Not recorded",
        )
        self.assertEqual(values["Age in 2022"], "45 years")
        senior = programs["SPISC"]["members"][-1]
        self.assertEqual(senior["cells"][0]["value"], "80 years")
        self.assertEqual(senior["cells"][1]["value"], "Yes — meaning needs confirmation")
        slp_values = str(programs["SLP"]["members"][-1]["cells"])
        self.assertIn("Self-employed without any paid employee", slp_values)

    def test_warning_codes_gaps_unknown_codes_and_missing_member_do_not_invent_answers(self):
        finding = self.source["findings"]["programs"]["SLP"]
        finding["screening_status"] = "Needs Data Review"
        finding["data_quality_warnings"] = tuple(
            {"code": code, "member_line_number": 1, "message": "PRIVATE_RAW_WARNING"}
            for code in WARNING_TEXT
        )
        result = self.present()[1]
        self.assertEqual(result["status"], "Needs Data Review")
        self.assertFalse(result["reasons"])
        for text in WARNING_TEXT.values():
            self.assertIn(text, str(result["issues"]))
        self.assertNotIn("PRIVATE_RAW_WARNING", str(result))
        self.source["members"] = []
        result = self.present()[1]
        self.assertIn("No household member records", str(result["issues"]))
        self.assertIn("Member not identified", str(result["supporting_context"]))
        self.assertTrue(result["differences"])

    def test_saved_evidence_differences_are_shown_and_do_not_replace_stored_status(self):
        self.source["members"][0]["responses"]["e01_employed"] = "1"
        program = self.present()[3]
        self.assertEqual(program["status"], "For Review")
        difference = program["differences"][0]
        self.assertEqual(difference["stored"], "Not recorded")
        self.assertEqual(difference["recorded"], "Yes")
        self.assertIn("differs", str(program["issues"]))

    def test_unknown_findings_and_missing_explanations_use_explicit_fallbacks(self):
        finding = self.source["findings"]["programs"]["4Ps"]
        finding["supporting_evidence"] = ()
        self.assertIn("specific supporting reason is not available", str(self.present()[0]["issues"]))
        finding["screening_status"] = "Needs Data Review"
        self.assertIn("does not identify one reliable cause", str(self.present()[0]["issues"]))
        finding["data_quality_warnings"] = ({"code": "future_unknown", "message": "RAW_TECHNICAL"},)
        finding["key_unverified_requirements"] = ("RAW_TECHNICAL",)
        result = self.present()[0]
        self.assertIn("unresolved information issue", str(result["issues"]))
        self.assertNotIn("RAW_TECHNICAL", str(result))

    def test_unconfirmed_answers_and_partial_work_information_remain_explicit(self):
        self.source["members"][0]["responses"].update(a07_age="NA", b06_ofi="77")
        slp = self.source["findings"]["programs"]["SLP"]
        slp["methodology_notes"] = (SLP_UNRESOLVED_NOTE,)
        slp["supporting_evidence"] = ({
            "member_line_number": 1, "pathway": "EF",
            "observed_values": {"a07_age": "NA", "b06_ofi": "77",
                                "e01_employed": "NA"},
        }, *slp["supporting_evidence"])
        result = self.present()[1]
        self.assertEqual(result["status"], "For Review")
        self.assertIn("Some work or livelihood information remains unclear", str(result["issues"]))
        self.assertIn("age is missing or unclear", str(result["issues"]))
        values = {cell["label"]: cell["value"] for cell in result["members"][0]["cells"]}
        self.assertEqual(values["Age in 2022"], "Not recorded")
        self.assertEqual(values["Overseas Filipino status"], "Meaning needs confirmation")
        self.assertNotIn("pathways", str(result))
        slp["screening_status"] = "Needs Data Review"
        self.assertNotIn("supports the For Review", str(self.present()[1]))

    def test_expected_work_skips_are_not_presented_as_specific_problems(self):
        finding = self.source["findings"]["programs"]["SLP"]
        finding.update(screening_status="Needs Data Review", supporting_evidence=(),
                       data_quality_warnings=(), methodology_notes=())
        member = self.source["members"][0]
        member["responses"].update(
            e01_employed="2", e11_nature_of_employment="NA",
            e18_class_of_worker="NA",
        )
        result = self.present()[1]
        issues = " ".join(result["issues"])
        self.assertIn("may have been skipped as expected", issues)
        self.assertNotIn("nature of work", issues.lower())
        self.assertNotIn("type of worker", issues.lower())
        self.assertEqual(result["status"], "Needs Data Review")

    def test_for_review_evidence_prioritizes_members_in_saved_findings(self):
        programs = {program["code"]: program for program in self.present()}
        self.assertEqual([member["name"] for member in programs["SLP"]["members"]],
                         ["Synthetic Member 3"])
        self.assertEqual([member["name"] for member in programs["TUPAD"]["members"]],
                         ["Synthetic Member 1", "Synthetic Member 3"])
        self.assertEqual([cell["label"] for cell in programs["SLP"]["members"][0]["cells"]], [
            "Age in 2022", "Overseas Filipino status",
            "Worked at least one hour during the survey reference week",
            "Type of worker",
        ])

    def test_member_named_for_unclear_age_remains_available_in_recorded_information(self):
        self.source["members"][0]["responses"]["a07_age"] = "NA"
        program = self.present()[0]
        self.assertIn("Synthetic Member 1: age is missing or unclear.", program["issues"])
        member = next(item for item in program["members"]
                      if item["name"] == "Synthetic Member 1")
        self.assertEqual(member["cells"], [{"label": "Age in 2022",
                                           "value": "Not recorded"}])

    def test_saved_household_corroboration_is_compared_with_original_record(self):
        finding = self.source["findings"]["programs"]["4Ps"]
        finding["supporting_evidence"] = ({
            "member_line_number": 1, "trigger_code": "member_pregnancy",
            "observed_values": {"l07_currently_pregnant": "2"},
            "corroborating_household_values": {"l06_with_member_currently_pregnant": "1"},
        },)
        result = self.present()[0]
        self.assertEqual(result["status"], "For Review")
        self.assertEqual(result["differences"][0]["stored"], "Yes")
        self.assertEqual(result["differences"][0]["recorded"], "No")


class ScreeningRouteTests(unittest.TestCase):
    def setUp(self):
        self.source = screening_source()
        self.user = {"id": 1, "username": "synthetic_staff", "role": "lgu_staff",
                     "is_active": True, "session_version": 1}
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48), "DATABASE_URL": None})
        self.client = self.app.test_client()
        for patcher in (
            patch("psycopg.connect", side_effect=AssertionError("No real connections")),
            patch("services.auth.get_connection", return_value=MagicMock()),
            patch("repositories.staff_users.find_by_id", side_effect=lambda *args: dict(self.user)),
            patch.object(assessment_details, "get_active_assessment_detail", return_value=self.source),
        ):
            self.query = patcher.start()
            self.addCleanup(patcher.stop)
        self.path = f"/assessments/{HOUSEHOLD_UUID}"

    def session(self):
        with self.client.session_transaction() as session:
            session.update(user_id=1, session_version=1, issued_at=int(time()), csrf_token="synthetic")

    def test_authorized_roles_can_open_tab_and_preserve_return_filters(self):
        for role in ("lgu_staff", "system_administrator"):
            self.user["role"] = role
            self.session()
            response = self.client.get(self.path + "?tab=screening&page=2&priority=secondary&program=4Ps&completeness=unknown")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.text.count('class="screening-evidence"'), 4)
            self.assertNotIn('<details class="screening-evidence" open', response.text)
            self.assertEqual(response.text.count(
                'class="detail-section screening-program screening-program--'
            ), 4)
            self.assertRegex(response.text, r'tab=screening[^>]+aria-current="page"')
            for href in re.findall(r'href="([^"]+)"', response.text):
                url = urlsplit(unescape(href))
                if url.path in (self.path, "/assessments") and url.query:
                    params = parse_qs(url.query)
                    self.assertEqual(params["page"], ["2"])
                    self.assertEqual(params["priority"], ["secondary"])
                    self.assertEqual(params["program"], ["4Ps"])
                    self.assertEqual(params["completeness"], ["unknown"])
            normalized = " ".join(response.text.split())
            for text in ("Synthetic Member 2", "80 years", "Self-employed",
                         "Why this household received For Review",
                         "View Recorded Information", "not a recorded No answer",
                         "do not determine official subsidy eligibility"):
                self.assertIn(text, normalized)
            self.assertNotIn("Still to verify", response.text)

    def test_all_statuses_render_with_no_ineligibility_claim_or_status_promotion(self):
        self.session()
        for code, status in zip(PROGRAMS, (*STATUSES, "For Review")):
            self.source["findings"]["programs"][code]["screening_status"] = status
        page = self.client.get(self.path + "?tab=screening").text
        self.assertIn("No Review Signal", page)
        self.assertIn("Not Enough Information", page)
        self.assertIn("do not determine official subsidy eligibility", " ".join(page.split()))
        article = re.search(r'<article[^>]+id="screening-spisc".*?</article>', page, re.S)[0]
        self.assertIn("Not Enough Information", article)
        self.assertNotIn("Needs Data Review", article)
        self.assertNotIn("Why this household received For Review", article)
        self.assertNotIn("screening-status--review", article)

    def test_source_names_are_escaped_and_technical_private_and_unimplemented_content_is_absent(self):
        self.session()
        self.source["members"][0]["responses"]["a01_first_name"] = "<script>alert(1)</script>"
        self.source["household"]["unrelated"] = "PRIVATE_UNRELATED_VALUE"
        page = self.client.get(self.path + "?tab=screening").text
        self.assertIn("&lt;script&gt;", page)
        for text in ("<script>alert", "PRIVATE_UNRELATED_VALUE", "INTERNAL_TECHNICAL",
                     "trigger_code", "e01_employed", "L06=", "provisional", "pilot",
                     "Synthetic Support", "Assessment Run", "Start Staff Review",
                     "General Welfare Review", "Still to verify",
                     "Official poverty-targeting classification"):
            self.assertNotIn(text, page)
        self.assertIn('tab=reviews', page)
        self.assertIn('>Staff Reviews</a>', page)
        self.assertNotIn('Review History', page)

    def test_unauthorized_sessions_never_load_screening_findings(self):
        self.assertEqual(self.client.get(self.path + "?tab=screening").location, "/login")
        for field, value in (("role", "unapproved"), ("is_active", False), ("session_version", 2)):
            with patch.dict(self.user, {field: value}):
                self.session()
                self.assertEqual(self.client.get(self.path + "?tab=screening").location, "/login")
        self.session()
        with self.client.session_transaction() as session:
            session["issued_at"] = 0
        self.assertEqual(self.client.get(self.path + "?tab=screening").location, "/login")
        self.query.assert_not_called()

    def test_screening_navigation_is_available_from_completed_tabs(self):
        self.session()
        for tab in ("overview", "mpi"):
            response = self.client.get(self.path + "?tab=" + tab)
            self.assertEqual(response.status_code, 200)
            self.assertIn("tab=screening", response.text)
            self.assertNotIn('class="screening-evidence"', response.text)
        self.assertEqual(self.client.get(self.path + "?tab=screening&tab=mpi").status_code, 400)
