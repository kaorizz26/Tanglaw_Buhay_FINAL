"""Accessible information-popup component and initial placement checks."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InfoPopupTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_shared_component_has_accessible_dialog_relationships(self):
        component = self.read("templates/components/info_popup.html")
        for expected in (
            'aria-label="More information about {{ topic }}"',
            'aria-haspopup="dialog"',
            'aria-labelledby="info-dialog-title"',
            'aria-describedby="info-dialog-body"',
            'data-info-dialog-close',
            'data-info-dialog-terms',
        ):
            self.assertIn(expected, component)

    def test_popup_supports_escape_and_returns_focus(self):
        script = self.read("static/js/info-popup.js")
        self.assertIn('event.key === "Escape"', script)
        self.assertIn("dialog.showModal()", script)
        self.assertIn("openingButton.focus()", script)
        self.assertIn('dialog.addEventListener("close"', script)
        self.assertIn('button.dataset.infoFormat === "terms"', script)
        self.assertIn('document.createElement("dt")', script)

    def test_import_form_button_rules_do_not_override_information_buttons(self):
        styles = self.read("static/css/data-import.css")
        self.assertIn(".import-form button:not(.info-button) {", styles)
        self.assertIn(
            ".import-upload-page .import-form button:not(.info-button) {", styles,
        )

    def test_first_recommended_topics_have_information_buttons(self):
        placements = {
            "templates/assessments/prioritization.html": (
                "ranking table columns",
            ),
            "templates/assessments/summary.html": (
                "municipal rank",
                "vulnerability score",
                "known-data coverage",
            ),
            "templates/assessments/screening.html": ("subsidy screening status",),
            "templates/assessments/staff_reviews.html": ("staff recommendation",),
            "templates/staff/data_import.html": ("same-year re-upload",),
            "templates/staff/import_batches.html": ("dataset statuses",),
            "templates/staff/_import_preview.html": (
                "blocking issues",
                "import warnings",
                "data coverage notes",
            ),
            "templates/assessments/progression_index.html": (
                "household progress status",
            ),
            "templates/staff/staff_accounts.html": (
                "staff roles and permissions",
            ),
            "templates/staff/staff_access.html": ("permission categories",),
        }
        for path, topics in placements.items():
            source = self.read(path)
            for topic in topics:
                with self.subTest(path=path, topic=topic):
                    self.assertIn(f"info_button('{topic}'", source)

        prioritization = self.read("templates/assessments/prioritization.html")
        self.assertEqual(prioritization.count("info_button("), 1)
        self.assertIn("About the ranking columns", prioritization)
        self.assertIn("ranking_columns_info, 'terms'", prioritization)


if __name__ == "__main__":
    unittest.main()
