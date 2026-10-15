"""Synthetic checks for evidence-supported reasons and safe attribution fallbacks."""

from copy import deepcopy
import unittest

from assessment_detail_fixtures import detail_source
from services.assessment_details import _indicator
from services.mpi_evidence import build_evidence_index
from services.vulnerability.health import FIES_FIELDS
from services.vulnerability.housing import ASSET_FIELDS


def member(age="45", **responses):
    row = detail_source()["members"][0]
    row["responses"].update(a07_age=age, b06_ofi="7", e01_employed="1",
                            c03_currently_attending_school="1", c02_hgc_group="7",
                            e15_want_more_hours="2")
    row["responses"].update(responses)
    return row


class AssessmentExplanationTests(unittest.TestCase):
    def explain(self, name, flag, household=None, members=None, source=None):
        source = deepcopy(source) if source is not None else detail_source()
        source["household"].update(household or {})
        if members is not None:
            source["members"] = deepcopy(members)
        before = deepcopy(source)
        stored = {**source["findings"]["mpi"], name: flag}
        result = _indicator(name, stored, source, build_evidence_index(source))
        self.assertEqual(source, before)
        self.assertEqual(result["flag"], flag)
        self.assertEqual(result["status"], "Unknown" if flag is None else (
            "Deprived" if flag else "Not Deprived"))
        self.assertEqual(result["contribution"] > 0, flag == 1)
        return result["explanation"].lower()

    def test_all_five_direct_indicators_use_decoded_answers_for_each_status(self):
        cases = (
            ("Water", "q01_main_water", "1", "3", "piped", "public tap"),
            ("Tenure", "r10_tenure", "1", "7", "own", "without consent"),
            ("Electricity", "r12_electricity", "1", "2", "yes", "no"),
            ("Internet", "n01_internet_access", "1", "2", "yes", "no"),
            ("Public Transportation", "h01_access_to_public_transportation", "1", "2", "yes", "no"),
        )
        for name, field, favorable, concern, yes_text, no_text in cases:
            for flag, raw, expected in ((0, favorable, yes_text), (1, concern, no_text),
                                        (None, "NA", "missing or unclear")):
                with self.subTest(name=name, flag=flag):
                    self.assertIn(expected, self.explain(name, flag, {field: raw}))

    def test_sanitation_and_housing_identify_only_mapped_causes(self):
        sanitation = self.explain("Sanitation", 1,
                                  {"q14_toilet_facility": "95", "q19_share_toilet": "1"})
        self.assertIn("no facility", sanitation)
        self.assertIn("shared with other households", sanitation)
        shared = self.explain("Sanitation", 1,
                             {"q14_toilet_facility": "12", "q19_share_toilet": "1"})
        self.assertNotIn("septic", shared)
        self.assertIn("shared with other households", shared)
        self.assertIn("not shared", self.explain("Sanitation", 0))
        self.assertIn("missing or unclear", self.explain("Sanitation", None,
                                                       {"q19_share_toilet": "NA"}))
        materials = self.explain("Housing Materials", 1,
                                 {"r04_outer_walls": "5", "r06_floor": "5"})
        self.assertIn("bamboo/sawali/cogon/nipa", materials)
        self.assertIn("earth/sand/mud", materials)
        self.assertNotIn("roof", materials)
        self.assertIn("none is classified", self.explain("Housing Materials", 0))
        self.assertIn("floor", self.explain("Housing Materials", None, {"r06_floor": "NA"}))

    def test_assets_food_and_living_space_explain_all_three_statuses(self):
        empty_assets = dict.fromkeys(ASSET_FIELDS, "0")
        self.assertIn("both communication", self.explain("Assets", 0))
        absent = self.explain("Assets", 1, empty_assets)
        for group in ("transportation", "communication", "durable"):
            self.assertIn(group, absent)
        self.assertIn("missing or unclear", self.explain(
            "Assets", None, {**empty_assets, "r16_n_mobile_phone": "NA",
                             "r16_a_refrigerator": "1"}))
        self.assertIn("transportation item", self.explain(
            "Assets", 0, {**empty_assets, "r16_u_motorcycle": "1"}))
        self.assertIn("4 of eight", self.explain("Food Insecurity", 1))
        self.assertIn("0 of eight", self.explain("Food Insecurity", 0,
                                               dict.fromkeys(FIES_FIELDS, "2")))
        partial = dict.fromkeys(FIES_FIELDS, "2")
        partial.update(dict.fromkeys(FIES_FIELDS[:3], "1"))
        partial[FIES_FIELDS[3]] = "NA"
        self.assertIn("1 missing or unclear", self.explain("Food Insecurity", None, partial))
        partial[FIES_FIELDS[2]] = "2"
        self.assertIn("even allowing", self.explain("Food Insecurity", 0, partial))
        self.assertIn("16.67 m² per person", self.explain("Overcrowding", 0))
        self.assertIn("1.00 m² per person", self.explain("Overcrowding", 1, {"r07_floor_area": "3"}))
        self.assertIn("0.00 m² per person", self.explain("Overcrowding", 1, {"r07_floor_area": "0"}))
        self.assertIn("floor area is missing", self.explain("Overcrowding", None, {"r07_floor_area": "NA"}))

    def test_school_and_attainment_distinguish_members_and_absent_populations(self):
        child = member("12")
        self.assertIn("member aged 5–17 is attending", self.explain("School Attendance", 0, members=[child]))
        child["responses"]["c03_currently_attending_school"] = "2"
        concern = self.explain("School Attendance", 1, members=[child])
        self.assertIn("synthetic member 1", concern)
        self.assertIn("age 12", concern)
        child["responses"]["c03_currently_attending_school"] = "NA"
        self.assertIn("school attendance is missing", self.explain("School Attendance", None, members=[child]))
        self.assertIn("no household members aged 5–17", self.explain("School Attendance", 0, members=[member()]))
        adult = member()
        self.assertIn("senior high school graduate", self.explain("Educational Attainment", 0, members=[adult]))
        adult["responses"]["c02_hgc_group"] = "3"
        self.assertIn("no recorded member aged 19", self.explain("Educational Attainment", 1, members=[adult]))
        adult["responses"]["c02_hgc_group"] = "NA"
        self.assertIn("highest-grade information is missing", self.explain("Educational Attainment", None, members=[adult]))
        self.assertIn("no household members aged 19", self.explain("Educational Attainment", 1, members=[child]))
        cohort = member("25", c02_hgc_group="5")
        text = self.explain("Educational Attainment", 0, members=[cohort])
        self.assertIn("junior high school graduate", text)
        self.assertNotIn("completed senior", text)

    def test_illness_uses_household_answer_member_treatment_and_actual_gaps(self):
        self.assertIn("no household illness", self.explain("Illness/Treatment Proxy", 0))
        adult = member()
        adult["health_responses"].update(l34_has_been_sick="1", l38_availed_medical_treatment="2")
        illness = {"l33_member_who_has_been_sick": "1"}
        self.assertIn("synthetic member 1", self.explain("Illness/Treatment Proxy", 1, illness, [adult]))
        adult["health_responses"]["l38_availed_medical_treatment"] = "1"
        self.assertIn("recorded yes for medical treatment", self.explain("Illness/Treatment Proxy", 0, illness, [adult]))
        adult["health_responses"]["l38_availed_medical_treatment"] = "NA"
        self.assertIn("medical treatment is not established", self.explain("Illness/Treatment Proxy", None, illness, [adult]))
        adult["health_responses"].update(l34_has_been_sick="2", l38_availed_medical_treatment="1")
        self.assertIn("conflict", self.explain("Illness/Treatment Proxy", None, illness, [adult]))
        adult.update(health_present=False, health_responses={})
        self.assertIn("follow-up records are missing", self.explain("Illness/Treatment Proxy", None, illness, [adult]))

    def test_adult_work_covers_counts_unknowns_skips_and_nonobserved_classifications(self):
        adult = member()
        self.assertIn("0 are classified", self.explain("Employment Proxy", 0, members=[adult]))
        adult["responses"]["e01_employed"] = "2"
        self.assertIn("1 is classified", self.explain("Employment Proxy", 1, members=[adult]))
        adult["responses"]["e01_employed"] = "NA"
        self.assertIn("past-week work information is missing", self.explain("Employment Proxy", None, members=[adult]))
        adult["responses"].update(e01_employed="1", e15_want_more_hours="2")
        self.assertIn("0 are recorded", self.explain("Underemployment Proxy", 0, members=[adult]))
        adult["responses"]["e15_want_more_hours"] = "1"
        self.assertIn("1 is recorded", self.explain("Underemployment Proxy", 1, members=[adult]))
        adult["responses"]["e15_want_more_hours"] = "NA"
        self.assertIn("additional-hours answers are missing", self.explain("Underemployment Proxy", None, members=[adult]))
        adult["responses"].update(b06_ofi="1", e01_employed="NA", e15_want_more_hours="1")
        text = self.explain("Employment Proxy", 0, members=[adult])
        self.assertIn("classified by the assessment", text)
        self.assertNotIn("recorded as working", text)
        self.assertIn("no applicable working adults", self.explain("Underemployment Proxy", 0, members=[adult]))
        adult["responses"]["e01_employed"] = "1"
        self.assertIn("conflict", self.explain("Employment Proxy", None, members=[adult]))
        for name in ("Employment Proxy", "Underemployment Proxy"):
            self.assertIn("no applicable", self.explain(name, 0, members=[member("12")]))

    def test_working_child_retains_joint_conditions_and_irrelevant_gaps(self):
        child = member("12", b06_ofi="NA")
        self.assertIn("recorded children are attending school", self.explain("Working Child Not in School", 0, members=[child]))
        child["responses"]["c03_currently_attending_school"] = "2"
        self.assertIn("age 12", self.explain("Working Child Not in School", 1, members=[child]))
        child["responses"]["e01_employed"] = "NA"
        self.assertIn("past-week work information is missing", self.explain("Working Child Not in School", None, members=[child]))
        child["responses"]["c03_currently_attending_school"] = "1"
        self.assertIn("none is established as both", self.explain("Working Child Not in School", 0, members=[child]))
        child["responses"]["a07_age"] = "NA"
        self.assertIn("potentially school-age", self.explain(
            "Working Child Not in School", 0, members=[child]))
        self.assertIn("no household members aged 5–17", self.explain("Working Child Not in School", 0, members=[member()]))

    def test_conflicting_mappings_and_disagreeing_evidence_never_invent_a_cause(self):
        source = detail_source()
        source["references"]["mpi_mapping"].append({
            "variable_name": "q19_share_toilet", "code": "1", "deprived": "0",
        })
        text = self.explain("Sanitation", 1, {"q19_share_toilet": "1"}, source=source)
        self.assertIn("do not identify a more specific reason", text)
        self.assertNotIn("toilet is shared", text)
        self.assertIn("missing or unclear", self.explain("Sanitation", None,
                                                       {"q19_share_toilet": "1"}, source=source))
        text = self.explain("Housing Materials", 0, {"r04_outer_walls": "5"})
        self.assertIn("result for housing materials is not deprived", text)
        self.assertNotIn("none is classified", text)
        text = self.explain("Food Insecurity", None)
        self.assertNotIn("missing", text)
        for name in ("School Attendance", "Educational Attainment", "Illness/Treatment Proxy",
                     "Employment Proxy", "Underemployment Proxy", "Working Child Not in School"):
            self.assertIn("unavailable", self.explain(name, None,
                                                    {"l33_member_who_has_been_sick": "1"}, []))
