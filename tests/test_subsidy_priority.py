"""Priority boundaries and validated filters use synthetic stored-summary values."""

from copy import deepcopy
import unittest

from werkzeug.datastructures import MultiDict

from services.assessment_filters import (
    AssessmentFilters, InvalidAssessmentFilters, normalize_filters, parse_filter_arguments,
)
from services.subsidy_priority import (
    NO_SUBSIDY_PRIORITY, PRIMARY_PRIORITY, SECONDARY_PRIORITY, derive_subsidy_priority,
)


class SubsidyPriorityTests(unittest.TestCase):
    def test_boundaries_and_each_supported_program(self):
        for program in ("4Ps", "SLP", "SPISC", "TUPAD"):
            statuses = {program: "For Review"}
            before = deepcopy(statuses)
            for score, expected in (
                (0, NO_SUBSIDY_PRIORITY), (19.999, NO_SUBSIDY_PRIORITY),
                (20, SECONDARY_PRIORITY), (25, SECONDARY_PRIORITY),
                (25.001, PRIMARY_PRIORITY), (100, PRIMARY_PRIORITY),
            ):
                with self.subTest(program=program, score=score):
                    self.assertEqual(derive_subsidy_priority(score, statuses), expected)
            self.assertEqual(statuses, before)

    def test_only_supported_for_review_signals_qualify(self):
        for statuses in (
            {}, {"4Ps": "Needs Data Review"}, {"SLP": "No Historical CBMS Trigger"},
            {"SPISC": None}, {"unrecognized": "For Review"},
        ):
            with self.subTest(statuses=statuses):
                self.assertEqual(derive_subsidy_priority(80, statuses), NO_SUBSIDY_PRIORITY)
        self.assertEqual(derive_subsidy_priority(26, {
            "4Ps": "Needs Data Review", "SLP": "For Review",
        }), PRIMARY_PRIORITY)

    def test_priority_filter_defaults_choices_and_pagination_parameters(self):
        default = parse_filter_arguments(MultiDict())
        self.assertEqual(default, AssessmentFilters())
        self.assertFalse(default.active)
        self.assertNotIn("priority", default.parameters)
        for choice in ("all", "both", "primary", "secondary", "other"):
            with self.subTest(choice=choice):
                filters = parse_filter_arguments(MultiDict([("priority", choice)]))
                self.assertEqual(filters.priority, choice)
                self.assertEqual(filters.active, choice != "all")
                self.assertEqual(filters.parameters, {} if choice == "all" else {"priority": choice})

    def test_priority_validation_rejects_invalid_and_repeated_parameters(self):
        for choice in (None, "", "Primary", "unknown", "primary\x00", [], 1):
            with self.subTest(choice=choice), self.assertRaises(InvalidAssessmentFilters):
                normalize_filters(AssessmentFilters(priority=choice))
        with self.assertRaises(InvalidAssessmentFilters):
            parse_filter_arguments(MultiDict([("priority", "all"), ("priority", "primary")]))

    def test_unknown_completeness_is_independent_of_priority(self):
        for choice in ("all", "both", "primary", "secondary", "other"):
            filters = parse_filter_arguments(MultiDict([
                ("priority", choice), ("completeness", "unknown"), ("program", "SLP"),
            ]))
            self.assertEqual(filters.priority, choice)
            self.assertEqual(filters.completeness, "unknown")
            self.assertEqual(filters.programs, ("SLP",))


if __name__ == "__main__":
    unittest.main()
