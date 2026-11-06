"""Public statistic candidates remain source-bound and unpublished by default."""

import unittest
from urllib.parse import urlparse

from services.public_statistics import (
    MISSING_SOURCE,
    PUBLIC_STATISTIC_GAPS,
    PUBLIC_STATISTIC_RECORDS,
    PUBLIC_STATISTIC_SOURCES,
    VERIFIED,
    get_public_statistics,
    validate_public_statistic_registry,
)


class PublicStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.records = {
            record["id"]: record
            for record in PUBLIC_STATISTIC_RECORDS
        }

    def test_registry_has_no_integrity_errors(self):
        self.assertEqual(validate_public_statistic_registry(), ())

    def test_new_records_are_not_published_before_ui_approval(self):
        self.assertTrue(PUBLIC_STATISTIC_RECORDS)
        self.assertTrue(all(
            not record["approved_for_public_display"]
            for record in PUBLIC_STATISTIC_RECORDS
        ))
        for program_slug in ("4ps", "spisc", "slp", "tupad"):
            with self.subTest(program_slug=program_slug):
                self.assertEqual(get_public_statistics(program_slug), ())
                self.assertTrue(get_public_statistics(
                    program_slug,
                    approved_only=False,
                ))

    def test_verified_records_use_registered_government_sources(self):
        allowed_domains = {
            "dole.gov.ph",
            "fo1.dswd.gov.ph",
            "pantawid.dswd.gov.ph",
            "pia.gov.ph",
            "ro1.dole.gov.ph",
            "www.dswd.gov.ph",
            "www.pangasinan.gov.ph",
        }

        for record in PUBLIC_STATISTIC_RECORDS:
            if record["verification_status"] != VERIFIED:
                continue
            with self.subTest(record_id=record["id"]):
                source = PUBLIC_STATISTIC_SOURCES[record["source_id"]]
                self.assertIn(urlparse(source["url"]).netloc, allowed_domains)
                self.assertEqual(record["verified_on"], "2026-11-06")

    def test_unlocated_pangasinan_4ps_figures_stay_unapproved(self):
        for record_id in (
                "4ps-pangasinan-active-households-2024",
                "4ps-pangasinan-self-sufficient-households-2025"):
            with self.subTest(record_id=record_id):
                record = self.records[record_id]
                self.assertEqual(record["verification_status"], MISSING_SOURCE)
                self.assertIsNone(record["source_id"])
                self.assertFalse(record["approved_for_public_display"])

    def test_spisc_source_discrepancy_is_preserved(self):
        record = self.records["spisc-region1-active-pensioners-2026"]

        self.assertEqual(record["value"], 201142)
        self.assertEqual(record["supplied_value"], 201170)
        self.assertIn("201,170", record["limitations"][0])
        self.assertIn("201,142", record["limitations"][0])

        new_pensioners = self.records["spisc-region1-new-pensioners-2026"]
        self.assertEqual(new_pensioners["value"], 16263)
        self.assertEqual(new_pensioners["related_record_id"], record["id"])
        self.assertIn("included", new_pensioners["limitations"][0])

    def test_4ps_verified_breakdowns_reconcile_to_source_totals(self):
        child_age_ids = (
            "4ps-region1-children-age-0-2-2025",
            "4ps-region1-children-age-3-5-2025",
            "4ps-region1-children-age-6-14-2025",
            "4ps-region1-children-age-15-18-2025",
        )
        education_ids = (
            "4ps-region1-education-kindergarten-2025",
            "4ps-region1-education-elementary-2025",
            "4ps-region1-education-junior-high-2025",
            "4ps-region1-education-senior-high-2025",
        )
        geography_ids = (
            "4ps-region1-geography-rural-2025",
            "4ps-region1-geography-urban-2025",
            "4ps-region1-geography-gida-2025",
        )

        self.assertEqual(
            sum(self.records[record_id]["value"] for record_id in child_age_ids),
            self.records["4ps-region1-children-0-18-2025"]["value"],
        )
        self.assertEqual(
            sum(self.records[record_id]["value"] for record_id in education_ids),
            self.records[
                "4ps-region1-regular-curriculum-children-2025"
            ]["value"],
        )
        self.assertEqual(
            sum(self.records[record_id]["value"] for record_id in geography_ids),
            self.records["4ps-region1-active-households-2025"]["value"],
        )
        self.assertEqual(
            self.records["4ps-philippines-female-grantees-2025"]["value"]
            + self.records["4ps-philippines-male-grantees-2025"]["value"],
            self.records["4ps-philippines-active-households-2025"]["value"],
        )

    def test_tupad_provincial_breakdown_reconciles_to_region_total(self):
        province_ids = (
            "tupad-pangasinan-workers-assisted-2025",
            "tupad-ilocos-norte-workers-assisted-2025",
            "tupad-la-union-workers-assisted-2025",
            "tupad-ilocos-sur-workers-assisted-2025",
        )

        self.assertEqual(
            sum(self.records[record_id]["value"] for record_id in province_ids),
            self.records["tupad-region1-workers-assisted-2025"]["value"],
        )

    def test_known_missing_categories_have_explicit_gaps(self):
        gap_keys = {
            (
                gap["program_slug"],
                gap["geography"],
                gap["category"],
            )
            for gap in PUBLIC_STATISTIC_GAPS
        }

        self.assertIn(("spisc", None, "demographics"), gap_keys)
        self.assertIn(("slp", None, "achievements"), gap_keys)
        self.assertIn(("tupad", "pozorrubio", "overview"), gap_keys)
        self.assertIn(("4ps", "pangasinan", "overview"), gap_keys)


if __name__ == "__main__":
    unittest.main()
