"""Historical screening tests use synthetic, non-identifying household records."""

import unittest
from unittest.mock import patch

import pandas as pd

from services.subsidy_screening import PROGRAMS, STATUSES, screen_households


class SubsidyScreeningTests(unittest.TestCase):
    def member(self, line=1, uuid="A", **changes):
        row = dict(uuid=uuid, line_number=line, a07_age=30, a05_sex=1,
                   a02_relation_to_hh_head=1, b06_ofi=7, e01_employed=1,
                   e11_nature_of_employment=1, e18_class_of_worker=1,
                   c07_tvet_graduate=2, c08_tvet_currently_attending=2)
        return row | changes

    def datasets(self, members=None, households=None, section=None):
        rows = [self.member()] if members is None else members
        households = households if households is not None else [dict(
            uuid="A", l01_with_member_who_has_been_pregnant=2,
            l06_with_member_currently_pregnant=2, l15_with_pwd_member=2)]
        return {
            "household": pd.DataFrame(households, dtype=object),
            "section_a_e_indiv": pd.DataFrame(rows, columns=list(self.member()), dtype=object),
            "section_l02_to_l41": pd.DataFrame(section or [], columns=[
                "uuid", "section_l_line_number", "l07_currently_pregnant", "l16_pwd"], dtype=object),
        }

    def result(self, program, members=None, households=None, section=None, year=2022):
        frame = screen_households(self.datasets(members, households, section), survey_year=year)
        return frame.loc[frame["program_code"].eq(program)].iloc[0]

    def test_4ps_age_zero_eighteen_and_nineteen_boundaries(self):
        for age, expected in ((0, "For Review"), (18, "For Review"), (19, "No Historical CBMS Trigger")):
            with self.subTest(age=age):
                result = self.result("4Ps", [self.member(a07_age=age)])
                self.assertEqual(result["screening_status"], expected)
                self.assertEqual(result["triggering_member_line_numbers"], (1,) if age <= 18 else ())

    def test_4ps_missing_or_invalid_age_is_unresolved(self):
        for age in (None, "NA", pd.NA, True, -1, 4.5):
            with self.subTest(age=age):
                self.assertEqual(self.result("4Ps", [self.member(a07_age=age)])["screening_status"], "Needs Data Review")

    def test_4ps_pregnancy_is_attributed_only_with_household_corroboration(self):
        households = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1,
                           l06_with_member_currently_pregnant=1)]
        section = [dict(uuid="A", section_l_line_number=1, l07_currently_pregnant=1, l16_pwd="NA")]
        result = self.result("4Ps", [self.member(a05_sex=2)], households, section)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], (1,))
        self.assertEqual(result["supporting_evidence"][1]["evidence_type"], "provisional_pilot")
        self.assertTrue(any("not PSA-confirmed" in note for note in result["methodology_notes"]))

    def test_4ps_absent_section_l_is_not_a_negative_pregnancy_answer(self):
        households = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1,
                           l06_with_member_currently_pregnant="NA")]
        self.assertEqual(self.result("4Ps", [self.member(a05_sex=2)], households)["screening_status"], "Needs Data Review")

    def test_4ps_verified_household_and_demographic_skips(self):
        for ever, current in ((2, "NA"), (1, 2)):
            households = [dict(uuid="A", l01_with_member_who_has_been_pregnant=ever,
                               l06_with_member_currently_pregnant=current)]
            self.assertEqual(self.result("4Ps", [self.member(a05_sex=2)], households)["screening_status"], "No Historical CBMS Trigger")
        self.assertEqual(self.result("4Ps", [self.member()], [dict(uuid="A")])["screening_status"], "No Historical CBMS Trigger")

    def test_4ps_conflicting_household_pregnancy_skip_remains_unresolved(self):
        households = [dict(uuid="A", l01_with_member_who_has_been_pregnant=2,
                           l06_with_member_currently_pregnant=1)]
        self.assertEqual(self.result("4Ps", [self.member(a05_sex=2)], households)["screening_status"], "Needs Data Review")

    def test_supported_child_wins_over_unresolved_pregnancy_and_unrelated_gaps(self):
        members = [self.member(a07_age=10), self.member(line=2, a07_age=None, a05_sex=2)]
        result = self.result("4Ps", members, [dict(uuid="A")])
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], (1,))

    def test_spisc_age_fifty_nine_sixty_and_multiple_members(self):
        self.assertEqual(self.result("SPISC", [self.member(a07_age=59)])["screening_status"], "No Historical CBMS Trigger")
        members = [self.member(a07_age=60), self.member(line=2, a07_age=67)]
        result = self.result("SPISC", members)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], (1, 2))

    def test_spisc_uncorroborated_pwd_is_neither_required_nor_attributed(self):
        section = [dict(uuid="A", section_l_line_number=1, l07_currently_pregnant="NA", l16_pwd=1)]
        result = self.result("SPISC", [self.member(a07_age=67)], section=section)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertNotIn("l16_pwd", result["supporting_evidence"][0]["observed_values"])
        self.assertEqual(result["data_quality_warnings"][0]["code"], "disability_member_conflict")
        self.assertEqual(self.result("SPISC", section=section)["screening_status"], "No Historical CBMS Trigger")
        self.assertEqual(self.result("SPISC", [self.member(a07_age=67)])["screening_status"], "For Review")

    def test_spisc_unknown_age_and_known_senior_precedence(self):
        self.assertEqual(self.result("SPISC", [self.member(a07_age=None)])["screening_status"], "Needs Data Review")
        self.assertEqual(self.result("SPISC", [self.member(a07_age=60), self.member(line=2, a07_age=None)])["screening_status"], "For Review")

    def test_slp_ef_requires_age_eighteen_and_observed_nonwork(self):
        for age, expected in ((17, "Needs Data Review"), (18, "For Review")):
            result = self.result("SLP", [self.member(a07_age=age, e01_employed=2, e18_class_of_worker="NA")])
            self.assertEqual(result["screening_status"], expected)
            self.assertEqual([item["pathway"] for item in result["supporting_evidence"]], ["EF"] if age == 18 else [])

    def test_slp_ef_section_e_routing_and_optional_tvet_support(self):
        for b06 in (4, 5, 7):
            result = self.result("SLP", [self.member(b06_ofi=b06, e01_employed=2, c07_tvet_graduate=1)])
            self.assertEqual(result["screening_status"], "For Review")
            self.assertIn("TVET graduate", result["trigger_reasons"][0])
        self.assertEqual(self.result("SLP", [self.member(e01_employed=2, b06_ofi=1)])["screening_status"], "No Historical CBMS Trigger")
        self.assertEqual(self.result("SLP", [self.member(e01_employed=2, b06_ofi="NA")])["screening_status"], "Needs Data Review")

    def test_slp_md_all_verified_enterprise_codes_and_age_boundaries(self):
        for code in (3, 4, 5, 6):
            for age in (15, 16, 17, 18):
                with self.subTest(code=code, age=age):
                    result = self.result("SLP", [self.member(a07_age=age, e18_class_of_worker=code)])
                    self.assertEqual(result["screening_status"], "For Review" if age >= 16 else "No Historical CBMS Trigger")
                    consent = any("guardian consent" in requirement for requirement in result["key_unverified_requirements"])
                    self.assertEqual(consent, age in (16, 17))
        for code in (0, 1, 2):
            self.assertEqual(self.result("SLP", [self.member(e18_class_of_worker=code)])["screening_status"], "No Historical CBMS Trigger")

    def test_slp_can_preserve_both_independent_pathways(self):
        members = [self.member(e01_employed=2, e18_class_of_worker="NA"), self.member(line=2, e18_class_of_worker=3)]
        result = self.result("SLP", members)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual([item["pathway"] for item in result["supporting_evidence"]], ["EF", "MD"])
        self.assertEqual(result["triggering_member_line_numbers"], (1, 2))

    def test_slp_does_not_use_imputation_and_tupad_derives_its_own_pilot_assumption(self):
        for b06 in (1, 7):
            data = self.datasets([self.member(b06_ofi=b06, e01_employed="NA", e18_class_of_worker="NA", e11_nature_of_employment="NA")])
            data["section_a_e_indiv"]["e15_want_more_hours"] = 1
            data["household"]["vulnerability_score"] = 100
            data["household"]["Employment Proxy"] = 1
            result = screen_households(data, survey_year=2022)
            slp = result.loc[result["program_code"].eq("SLP")].iloc[0]
            self.assertNotEqual(slp["screening_status"], "For Review")
            self.assertEqual(slp["supporting_evidence"], ())
            tupad = result.loc[result["program_code"].eq("TUPAD")].iloc[0]
            self.assertEqual(tupad["screening_status"], "For Review" if b06 == 7 else "No Historical CBMS Trigger")
            self.assertEqual(tupad["supporting_evidence"][0]["evidence_type"], "pilot_assumption")
            data["section_a_e_indiv"]["e15_want_more_hours"] = 2
            data["household"]["vulnerability_score"] = 0
            data["household"]["Employment Proxy"] = 0
            pd.testing.assert_frame_equal(result, screen_households(data, survey_year=2022))

    def test_md_cannot_manufacture_verified_work_from_uncertain_routing(self):
        members = [self.member(a07_age=16, e01_employed="NA", e18_class_of_worker=3)]
        self.assertEqual(self.result("SLP", members)["screening_status"], "Needs Data Review")

    def test_tupad_irregular_work_codes_and_minimum_age_without_upper_limit(self):
        for code in (2, 3):
            for age in (17, 18, 80):
                with self.subTest(code=code, age=age):
                    result = self.result("TUPAD", [self.member(a07_age=age, e11_nature_of_employment=code)])
                    self.assertEqual(result["screening_status"], "For Review" if age >= 18 else "No Historical CBMS Trigger")

    def test_tupad_permanent_work_is_negative_but_missing_history_is_unresolved(self):
        self.assertEqual(self.result("TUPAD")["screening_status"], "No Historical CBMS Trigger")
        for work, nature in (("NA", "NA"), ("NA", 2), (1, 999)):
            self.assertEqual(self.result("TUPAD", [self.member(e01_employed=work, e11_nature_of_employment=nature)])["screening_status"], "Needs Data Review")

    def test_tupad_observed_nonwork_is_an_independent_trigger(self):
        result = self.result("TUPAD", [self.member(e01_employed=2, e11_nature_of_employment="NA")])
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], (1,))
        self.assertEqual(result["supporting_evidence"][0]["trigger_code"], "observed_nonwork")

    def test_tupad_observed_and_assumed_nonwork_have_distinct_evidence(self):
        for b06 in (4, 5, 7):
            for work, kind, code in ((2, "observed", "observed_nonwork"), ("NA", "pilot_assumption", "assumed_nonwork")):
                result = self.result("TUPAD", [self.member(b06_ofi=b06, e01_employed=work,
                    e11_nature_of_employment="NA", e18_class_of_worker="NA")])
                self.assertEqual(result["screening_status"], "For Review")
                item = result["supporting_evidence"][0]
                self.assertEqual(item["evidence_type"], kind)
                self.assertEqual(item["trigger_code"], code)
                self.assertEqual(item["observed_values"]["e01_employed"], work)
                if kind == "pilot_assumption":
                    self.assertIn("ASSUMED", item["explanation"])

    def test_tupad_missing_tokens_are_imputed_but_invalid_codes_are_unresolved(self):
        for work in (None, "", pd.NA, " na "):
            result = self.result("TUPAD", [self.member(e01_employed=work,
                e11_nature_of_employment="NA", e18_class_of_worker="NA")])
            self.assertEqual(result["screening_status"], "For Review")
        for work in (True, 0, 999):
            self.assertEqual(self.result("TUPAD", [self.member(e01_employed=work)])["screening_status"], "Needs Data Review")

    def test_tupad_ofw_assumption_and_other_skips_do_not_trigger_nonwork(self):
        for b06 in (1, 2, 3, 6):
            result = self.result("TUPAD", [self.member(b06_ofi=b06, e01_employed="NA",
                e11_nature_of_employment="NA", e18_class_of_worker="NA")])
            self.assertEqual(result["screening_status"], "No Historical CBMS Trigger")
            self.assertEqual(result["triggering_member_line_numbers"], ())
            self.assertEqual(result["trigger_reasons"], ())
            if b06 != 6:
                self.assertEqual(result["supporting_evidence"][0]["trigger_code"], "assumed_overseas_work")
                self.assertEqual(result["supporting_evidence"][0]["evidence_role"], "supporting_context")

    def test_tupad_contradictory_work_routing_is_flagged_without_forced_imputation(self):
        for b06 in (1, 2, 3):
            result = self.result("TUPAD", [self.member(b06_ofi=b06, e01_employed=2)])
            self.assertEqual(result["screening_status"], "Needs Data Review")
            self.assertEqual(result["data_quality_warnings"][0]["code"], "employment_skip_conflict")
        result = self.result("TUPAD", [self.member(e01_employed="NA", e11_nature_of_employment=2)])
        self.assertEqual(result["screening_status"], "Needs Data Review")
        self.assertEqual(result["data_quality_warnings"][0]["code"], "nonwork_imputation_conflict")
        result = self.result("TUPAD", [self.member(b06_ofi=6, e01_employed=2)])
        self.assertEqual(result["screening_status"], "No Historical CBMS Trigger")
        self.assertTrue(result["data_quality_warnings"])

    def test_tupad_age_boundaries_unknown_routing_and_independent_trigger_precedence(self):
        for age, expected in ((17, "No Historical CBMS Trigger"), (18, "For Review"), (80, "For Review"), (None, "Needs Data Review")):
            result = self.result("TUPAD", [self.member(a07_age=age, e01_employed="NA",
                e11_nature_of_employment="NA", e18_class_of_worker="NA")])
            self.assertEqual(result["screening_status"], expected)
        self.assertEqual(self.result("TUPAD", [self.member(b06_ofi="NA", e01_employed=2)])["screening_status"], "Needs Data Review")
        members = [self.member(e01_employed=2, e11_nature_of_employment="NA"), self.member(line=2, a07_age=None)]
        self.assertEqual(self.result("TUPAD", members)["screening_status"], "For Review")

    def test_4ps_household_pregnancy_triggers_without_member_attribution(self):
        hh = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1, l06_with_member_currently_pregnant=1)]
        result = self.result("4Ps", [self.member(a05_sex=2)], hh)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], ())
        self.assertEqual(result["supporting_evidence"][0]["source_dataset"], "household")
        self.assertEqual(result["supporting_evidence"][0]["trigger_code"], "household_pregnancy")
        self.assertTrue(result["data_quality_warnings"])
        self.assertIn("2022", result["trigger_reasons"][0])

    def test_4ps_l07_positive_needs_l06_and_valid_female_age_applicability(self):
        section = [dict(uuid="A", section_l_line_number=1, l07_currently_pregnant=1)]
        for l06 in (2, "NA"):
            hh = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1, l06_with_member_currently_pregnant=l06)]
            result = self.result("4Ps", [self.member(a05_sex=2)], hh, section)
            self.assertEqual(result["screening_status"], "Needs Data Review")
            self.assertEqual(result["triggering_member_line_numbers"], ())
            self.assertTrue(result["data_quality_warnings"])
        hh = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1, l06_with_member_currently_pregnant=1)]
        for sex, age in ((1, 30), (2, 9), (None, 30), (2, None)):
            result = self.result("4Ps", [self.member(a05_sex=sex, a07_age=age)], hh, section)
            self.assertFalse(any(item.get("trigger_code") == "member_pregnancy" for item in result["supporting_evidence"]))
            self.assertTrue(any(item["code"] == "pregnancy_member_conflict" for item in result["data_quality_warnings"]))

    def test_4ps_provisional_attribution_uses_own_joined_member_line(self):
        members = [self.member(), self.member(line=2, a05_sex=2)]
        hh = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1, l06_with_member_currently_pregnant=1)]
        section = [dict(uuid="A", section_l_line_number=2, l07_currently_pregnant=1),
                   dict(uuid="A", section_l_line_number=1, l07_currently_pregnant=2)]
        result = self.result("4Ps", members, hh, section)
        self.assertEqual(result["triggering_member_line_numbers"], (2,))
        self.assertFalse(result["data_quality_warnings"])

    def test_spisc_household_disability_is_not_automatically_senior_disability(self):
        members = [self.member(a07_age=70), self.member(line=2, a07_age=20)]
        hh = [dict(uuid="A", l15_with_pwd_member=1)]
        section = [dict(uuid="A", section_l_line_number=2, l16_pwd=1),
                   dict(uuid="A", section_l_line_number=1, l16_pwd=2)]
        result = self.result("SPISC", members, hh, section)
        self.assertEqual(result["screening_status"], "For Review")
        self.assertEqual(result["triggering_member_line_numbers"], (1,))
        self.assertTrue(any(item.get("trigger_code") == "household_disability" for item in result["supporting_evidence"]))
        self.assertFalse(any(item.get("trigger_code") == "senior_disability" for item in result["supporting_evidence"]))

    def test_spisc_disability_is_senior_specific_and_household_corroborated(self):
        members = [self.member(a07_age=60), self.member(line=2, a07_age=70)]
        hh = [dict(uuid="A", l15_with_pwd_member=1)]
        section = [dict(uuid="A", section_l_line_number=2, l16_pwd=1),
                   dict(uuid="A", section_l_line_number=1, l16_pwd=2)]
        result = self.result("SPISC", members, hh, section)
        supporting = [item for item in result["supporting_evidence"] if item.get("trigger_code") == "senior_disability"]
        self.assertEqual([item["member_line_number"] for item in supporting], [2])
        self.assertEqual(supporting[0]["evidence_type"], "provisional_pilot")
        self.assertEqual(supporting[0]["corroborating_household_values"], {"l15_with_pwd_member": 1})
        self.assertEqual(result["triggering_member_line_numbers"], (1, 2))

    def test_spisc_supporting_disability_does_not_create_an_age_trigger(self):
        result = self.result("SPISC", [self.member(a07_age=59)], [dict(uuid="A", l15_with_pwd_member=1)],
                             [dict(uuid="A", section_l_line_number=1, l16_pwd=1)])
        self.assertEqual(result["screening_status"], "No Historical CBMS Trigger")
        self.assertTrue(result["supporting_evidence"])
        self.assertEqual(result["triggering_member_line_numbers"], ())
        self.assertEqual(result["trigger_reasons"], ())

    def test_spisc_disability_gaps_and_conflicts_do_not_change_age_status(self):
        for l15, l16, expected in ((1, "NA", "senior_disability_unresolved"),
                                  (2, 1, "disability_member_conflict"),
                                  ("NA", 1, "disability_member_conflict"),
                                  (1, 999, "disability_invalid_member_code")):
            result = self.result("SPISC", [self.member(a07_age=70)], [dict(uuid="A", l15_with_pwd_member=l15)],
                                 [dict(uuid="A", section_l_line_number=1, l16_pwd=l16)])
            self.assertEqual(result["screening_status"], "For Review")
            self.assertTrue(any(item["code"] == expected for item in result["data_quality_warnings"]))
            self.assertFalse(any(item.get("trigger_code") == "senior_disability" for item in result["supporting_evidence"]))

    def test_household_yes_with_complete_member_no_responses_records_conflicts(self):
        hh = [dict(uuid="A", l01_with_member_who_has_been_pregnant=1,
                   l06_with_member_currently_pregnant=1, l15_with_pwd_member=1)]
        section = [dict(uuid="A", section_l_line_number=1, l07_currently_pregnant=2, l16_pwd=2)]
        data = self.datasets([self.member(a07_age=70, a05_sex=2)], hh, section)
        result = screen_households(data, survey_year=2022)
        four_ps = result.loc[result["program_code"].eq("4Ps")].iloc[0]
        self.assertEqual(four_ps["screening_status"], "For Review")
        self.assertEqual(four_ps["triggering_member_line_numbers"], ())
        self.assertEqual(four_ps["data_quality_warnings"][0]["code"], "pregnancy_household_member_conflict")
        spisc = result.loc[result["program_code"].eq("SPISC")].iloc[0]
        self.assertEqual(spisc["screening_status"], "For Review")
        self.assertEqual(spisc["data_quality_warnings"][0]["code"], "disability_household_member_conflict")
        self.assertFalse(any(item.get("trigger_code") == "senior_disability" for item in spisc["supporting_evidence"]))

    def test_one_household_can_have_all_four_independent_flags(self):
        members = [self.member(a07_age=10), self.member(line=2, a07_age=65, e01_employed=2),
                   self.member(line=3, e11_nature_of_employment=2, e18_class_of_worker=3)]
        result = screen_households(self.datasets(members), survey_year=2022)
        self.assertEqual(result["program_code"].tolist(), list(PROGRAMS))
        self.assertEqual(result["screening_status"].tolist(), ["For Review"] * 4)

    def test_household_member_lines_remain_separate_with_shuffled_section_l(self):
        members = [self.member(line=2, a07_age=12), self.member(uuid="B", a07_age=61)]
        households = [dict(uuid="B"), dict(uuid="A")]
        section = [dict(uuid="B", section_l_line_number=1, l07_currently_pregnant="NA", l16_pwd=1),
                   dict(uuid="A", section_l_line_number=2, l07_currently_pregnant=2, l16_pwd="NA")]
        result = screen_households(self.datasets(members, households, section), survey_year=2022)
        self.assertEqual(result["household_uuid"].tolist(), ["B"] * 4 + ["A"] * 4)
        self.assertEqual(result.loc[result["program_code"].eq("4Ps"), "triggering_member_line_numbers"].tolist(), [(), (2,)])
        self.assertEqual(result.loc[result["program_code"].eq("SPISC"), "triggering_member_line_numbers"].tolist(), [(1,), ()])
        self.assertFalse(result.duplicated(["household_uuid", "program_code"]).any())

    def test_ambiguous_keys_and_orphan_section_l_rows_are_rejected(self):
        data = self.datasets()
        broken = self.datasets()
        broken["household"] = pd.concat([data["household"], data["household"]])
        with self.assertRaises(ValueError):
            screen_households(broken, survey_year=2022)
        for name in ("section_a_e_indiv", "section_l02_to_l41"):
            broken = self.datasets(section=[dict(uuid="A", section_l_line_number=1)])
            broken[name] = pd.concat([broken[name], broken[name]])
            with self.assertRaises(ValueError):
                screen_households(broken, survey_year=2022)
        for line in (None, "", 999):
            broken = self.datasets(section=[dict(uuid="A", section_l_line_number=line)])
            with self.assertRaises(ValueError):
                screen_households(broken, survey_year=2022)
        broken = self.datasets([self.member(uuid="B")])
        with self.assertRaises(ValueError):
            screen_households(broken, survey_year=2022)

    def test_absent_roster_is_unknown_for_all_programs(self):
        result = screen_households(self.datasets([]), survey_year=2022)
        self.assertEqual(result["screening_status"].tolist(), ["Needs Data Review"] * 4)

    def test_unverified_administrative_requirements_do_not_make_known_negatives_unknown(self):
        result = screen_households(self.datasets(), survey_year=2022)
        self.assertEqual(result["screening_status"].tolist(), ["No Historical CBMS Trigger"] * 4)
        self.assertTrue(all(result["key_unverified_requirements"].map(bool)))
        self.assertFalse(any("task" in column or "eligibility_score" in column for column in result.columns))

    def test_survey_year_is_explicit_and_never_changes_recorded_age(self):
        for year in (2022, 2030):
            result = self.result("SPISC", [self.member(a07_age=59)], year=year)
            self.assertEqual(result["survey_year"], year)
            self.assertEqual(result["screening_status"], "No Historical CBMS Trigger")
        result = self.result("4Ps", [self.member(a07_age=18)], year=2030)
        self.assertIn("2030", result["trigger_reasons"][0])
        for year in (None, True, "2022", 2022.0, 0):
            with self.assertRaises(ValueError):
                screen_households(self.datasets(), survey_year=year)

    def test_sources_are_unchanged_and_screening_does_not_call_mpi(self):
        data = self.datasets()
        originals = {name: frame.copy(deep=True) for name, frame in data.items()}
        with patch("services.vulnerability.scoring.assess_households", side_effect=AssertionError("MPI must not run")):
            result = screen_households(data, survey_year=2022)
        for name in data:
            pd.testing.assert_frame_equal(data[name], originals[name])
        self.assertTrue(result["screening_status"].isin(STATUSES).all())
        self.assertNotIn("vulnerability_score", result)


if __name__ == "__main__":
    unittest.main()
