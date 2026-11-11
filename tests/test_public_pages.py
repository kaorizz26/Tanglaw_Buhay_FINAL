"""Public information pages remain separate from staff and CBMS access."""

import re
import unittest
from unittest.mock import patch

from app import create_app
from services.public_programs import (
    FOUR_PS_COVERAGE_REPORT_URL,
    PUBLIC_PROGRAMS,
    RESEARCH_ESTIMATE,
)


class PublicPagesTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True, "DATABASE_URL": None, "SECRET_KEY": None,
        })
        self.client = self.app.test_client()

    def test_anonymous_pages_need_no_staff_session_or_database(self):
        with patch("services.auth.get_connection") as connection:
            landing = self.client.get("/public")
            self.assertEqual(landing.status_code, 200)
            self.assertIn("Tanglaw-Buhay", landing.text)
            self.assertIn("tanglaw-buhay-logo-dark.png", landing.text)
            self.assertNotIn("assets/logos/logo_B.png", landing.text)
            self.assertNotIn("program-statistic-card", landing.text)
            self.assertEqual(
                landing.text.count('class="program-card__link"'),
                len(PUBLIC_PROGRAMS),
            )
            for slug in PUBLIC_PROGRAMS:
                with self.subTest(slug=slug):
                    page = self.client.get(f"/public/programs/{slug}")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn(PUBLIC_PROGRAMS[slug]["name"], page.text)
                    self.assertIn("tanglaw-buhay-logo-dark.png", page.text)
                    self.assertNotIn("assets/logos/logo_B.png", page.text)
                    self.assertIn('href="/public#programs"', page.text)
            connection.assert_not_called()

    def test_program_pages_use_compact_back_button_in_header(self):
        for slug in PUBLIC_PROGRAMS:
            with self.subTest(slug=slug):
                page = self.client.get(f"/public/programs/{slug}")
                translation_key = "fourps" if slug == "4ps" else slug
                header_start = page.text.index('<header class="fourps-header">')
                button_position = page.text.index(
                    'class="fourps-back-button"',
                )
                heading_position = page.text.index(
                    f'data-i18n="{translation_key}.title"',
                )

                self.assertNotIn("fourps-backbar", page.text)
                self.assertNotIn("fourps-back-link", page.text)
                self.assertIn(
                    'data-i18n-aria-label="programDetail.back"',
                    page.text,
                )
                self.assertIn(
                    f'data-i18n="{translation_key}.back">Bumalik</span>',
                    page.text,
                )
                self.assertLess(header_start, button_position)
                self.assertLess(button_position, heading_position)

    def test_program_pages_use_one_help_panel_with_official_facebook_link(self):
        facebook_url = "https://www.facebook.com/share/1EbibfythW/"

        for slug in PUBLIC_PROGRAMS:
            with self.subTest(slug=slug):
                page = self.client.get(f"/public/programs/{slug}")
                translation_key = "fourps" if slug == "4ps" else slug

                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.text.count('class="fourps-help"'), 1)
                self.assertNotIn("fourps-question", page.text)
                self.assertNotIn("(075) 556-7015", page.text)
                self.assertIn(f'href="{facebook_url}"', page.text)
                self.assertIn('target="_blank"', page.text)
                self.assertIn('rel="noopener noreferrer"', page.text)
                self.assertIn(
                    f'data-i18n="{translation_key}.help.facebookAction"',
                    page.text,
                )

    def test_research_estimates_are_clearly_labeled_and_published(self):
        expected_estimates = {
            "4ps": (1833, "1,833", (1100, 641, 92)),
            "spisc": (2450, "2,450", (1470, 858, 122)),
            "slp": (176, "176", (106, 61, 9)),
            "tupad": (1258, "1,258", (755, 440, 63)),
        }

        for slug, details in expected_estimates.items():
            with self.subTest(slug=slug):
                value, display_value, priority_values = details
                statistic = PUBLIC_PROGRAMS[slug]["statistic"]
                self.assertEqual(
                    statistic["publication_status"],
                    RESEARCH_ESTIMATE,
                )
                self.assertEqual(statistic["value"], value)
                self.assertTrue(statistic["approved_for_public_display"])
                self.assertEqual(
                    sum(item["value"] for item in statistic["priority_breakdown"]),
                    value,
                )
                self.assertEqual(
                    sum(
                        item["percentage"]
                        for item in statistic["priority_breakdown"]
                    ),
                    100,
                )
                self.assertEqual(
                    tuple(
                        item["value"]
                        for item in statistic["priority_breakdown"]
                    ),
                    priority_values,
                )

                page = self.client.get(f"/public/programs/{slug}")
                self.assertEqual(page.status_code, 200)
                self.assertIn("program-estimate-dashboard", page.text)
                self.assertNotIn("program-statistic-card--estimate", page.text)
                self.assertNotIn("program-statistic-pending", page.text)
                self.assertIn(f"<strong>{display_value}</strong>", page.text)
                self.assertIn("Tantiyang batay sa pananaliksik", page.text)
                self.assertIn("CBMS data", page.text)
                self.assertIn("Hindi ito opisyal na tala", page.text)

    def test_estimate_dashboard_explains_values_and_limitations(self):
        expected_units = {
            "4ps": "Mga sambahayan",
            "spisc": "Mga indibidwal",
            "slp": "Mga sambahayan",
            "tupad": "Mga indibidwal",
        }

        for slug, expected_unit in expected_units.items():
            with self.subTest(slug=slug):
                page = self.client.get(f"/public/programs/{slug}")

                self.assertEqual(page.status_code, 200)
                self.assertEqual(
                    page.text.count('class="program-estimate-metric '),
                    4,
                )
                self.assertEqual(
                    page.text.count(
                        'class="program-estimate-chart__segment '
                    ),
                    3,
                )
                self.assertIn(
                    'data-i18n-aria-label="programData.metricsLabel"',
                    page.text,
                )
                self.assertIn('aria-describedby=', page.text)
                self.assertIn("Tungkol sa datos na ito", page.text)
                self.assertIn("Pozorrubio, Pangasinan", page.text)
                self.assertIn(expected_unit, page.text)
                self.assertIn("Hindi pa nakukumpirma", page.text)
                self.assertIn("60%/35%/5%", page.text)
                self.assertIn("palagay ng proyekto", page.text)

    def test_public_translation_keys_exist_in_both_languages(self):
        javascript_response = self.client.get("/static/js/public.js")
        try:
            javascript = javascript_response.text
        finally:
            javascript_response.close()
        filipino_block = javascript.split("        fil: {", 1)[1].split(
            "        en: {", 1,
        )[0]
        english_block = javascript.split("        en: {", 1)[1].split(
            "\n        },\n    };", 1,
        )[0]
        key_pattern = re.compile(r'^\s*"([^"]+)":', re.MULTILINE)
        filipino_keys = set(key_pattern.findall(filipino_block))
        english_keys = set(key_pattern.findall(english_block))

        pages = [self.client.get("/public")]
        pages.extend(
            self.client.get(f"/public/programs/{slug}")
            for slug in PUBLIC_PROGRAMS
        )

        referenced_keys = set()
        try:
            for page in pages:
                referenced_keys.update(re.findall(
                    r'data-i18n(?:-aria-label)?="([^"]+)"',
                    page.text,
                ))
        finally:
            for page in pages:
                page.close()

        self.assertEqual(referenced_keys - filipino_keys, set())
        self.assertEqual(referenced_keys - english_keys, set())
        self.assertEqual(filipino_keys, english_keys)

    def test_verified_4ps_reference_is_retained_internally(self):
        four_ps_statistic = PUBLIC_PROGRAMS["4ps"]["statistic"]

        self.assertEqual(
            four_ps_statistic["official_reference"]["source_url"],
            FOUR_PS_COVERAGE_REPORT_URL,
        )
        page = self.client.get("/public/programs/4ps")
        self.assertNotIn(FOUR_PS_COVERAGE_REPORT_URL, page.text)

    def test_geography_selector_defaults_to_pozorrubio(self):
        expected_geographies = (
            "pozorrubio",
            "pangasinan",
            "region",
            "philippines",
        )

        for slug in PUBLIC_PROGRAMS:
            with self.subTest(slug=slug):
                page = self.client.get(f"/public/programs/{slug}")

                self.assertEqual(page.status_code, 200)
                self.assertIn("data-statistics-tabs", page.text)
                self.assertIn("Estadistika at Demograpiko", page.text)
                self.assertNotIn("Datos sa Pozorrubio", page.text)
                self.assertEqual(page.text.count("data-statistics-tab="), 4)
                self.assertEqual(page.text.count("data-statistics-panel="), 4)
                self.assertEqual(page.text.count('aria-selected="true"'), 1)
                self.assertEqual(page.text.count('aria-selected="false"'), 3)
                self.assertEqual(page.text.count("data-statistics-show="), 3)

                for geography in expected_geographies:
                    self.assertIn(
                        f'data-statistics-tab="{geography}"',
                        page.text,
                    )
                    self.assertIn(
                        f'data-statistics-panel="{geography}"',
                        page.text,
                    )

                self.assertIn(
                    "Hindi kami awtomatikong gumagamit ng datos mula sa "
                    "ibang lugar bilang kapalit.",
                    page.text,
                )

    def test_unknown_program_and_fixture_boundary(self):
        self.assertEqual(self.client.get("/public/programs/unknown").status_code, 404)
        with patch.dict(self.app.extensions["development_workspace"],
                        source_kind="actual_cbms"):
            self.assertEqual(self.client.get("/public").status_code, 403)
            self.assertEqual(self.client.get("/public/programs/4ps").status_code, 403)

    def test_staff_routes_remain_protected(self):
        self.assertEqual(self.client.get("/").status_code, 503)
        self.app.config["SECRET_KEY"] = "synthetic-test-key-" * 3
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")

    def test_public_assets_are_served(self):
        paths = [
            "css/public.css", "js/public.js",
            "assets/logos/tanglaw-buhay-logo-light.png",
            "assets/logos/tanglaw-buhay-logo-dark.png",
            "assets/images/header_backdrop.jpg",
            *(details["icon"] for details in PUBLIC_PROGRAMS.values()),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(f"/static/{path}")
                self.assertEqual(response.status_code, 200)
                response.close()


if __name__ == "__main__":
    unittest.main()
