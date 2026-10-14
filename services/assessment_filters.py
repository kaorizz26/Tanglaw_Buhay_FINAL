"""Validated presentation filters for stored household assessment summaries."""

from dataclasses import dataclass


PROGRAM_ORDER = ("4Ps", "SLP", "SPISC", "TUPAD")
COMPLETENESS_OPTIONS = (
    ("all", "All Households"),
    ("determined", "All 16 Indicators Determined"),
    ("unknown", "With Unknown Indicators"),
)
MAX_SEARCH_LENGTH = 200
MAX_BARANGAY_LENGTH = 200
SUBSIDY_MODES = ("any", "exact")


class InvalidAssessmentFilters(ValueError):
    """Invalid filter input without echoing private search values."""


@dataclass(frozen=True)
class AssessmentFilters:
    """One immutable selection, with broad or exact stored subsidy matching."""

    search: str = ""
    barangay: str = ""
    programs: tuple[str, ...] = ()
    completeness: str = "all"
    subsidy_mode: str = "any"

    @property
    def active(self):
        return bool(self.search or self.barangay or self.programs or self.completeness != "all")

    @property
    def parameters(self):
        """Return only active GET parameters for stable pagination links."""
        values = {}
        if self.search:
            values["q"] = self.search
        if self.barangay:
            values["barangay"] = self.barangay
        if self.programs:
            values["program"] = self.programs
        if self.subsidy_mode != "any":
            values["subsidy_mode"] = self.subsidy_mode
        if self.completeness != "all":
            values["completeness"] = self.completeness
        return values


def normalize_filters(filters=None):
    """Validate and normalize input before any database connection is opened."""
    if filters is None:
        return AssessmentFilters()
    if not isinstance(filters, AssessmentFilters):
        raise InvalidAssessmentFilters("invalid_assessment_filters")
    if (
        not isinstance(filters.search, str)
        or len(filters.search) > MAX_SEARCH_LENGTH
        or "\x00" in filters.search
        or not isinstance(filters.barangay, str)
        or len(filters.barangay) > MAX_BARANGAY_LENGTH
        or "\x00" in filters.barangay
        or not isinstance(filters.programs, (tuple, list))
        or len(filters.programs) > len(PROGRAM_ORDER)
        or any(program not in PROGRAM_ORDER for program in filters.programs)
        or not isinstance(filters.completeness, str)
        or filters.completeness not in dict(COMPLETENESS_OPTIONS)
        or not isinstance(filters.subsidy_mode, str)
        or filters.subsidy_mode not in SUBSIDY_MODES
    ):
        raise InvalidAssessmentFilters("invalid_assessment_filters")
    return AssessmentFilters(
        search=" ".join(filters.search.split()),
        barangay=filters.barangay.strip(),
        programs=tuple(program for program in PROGRAM_ORDER if program in filters.programs),
        completeness=filters.completeness,
        subsidy_mode=filters.subsidy_mode,
    )


def parse_filter_arguments(arguments):
    """Read GET selections, rejecting ambiguous repeated scalar parameters."""
    values = {}
    for parameter, default in (
        ("q", ""), ("barangay", ""), ("completeness", "all"), ("subsidy_mode", "any"),
    ):
        supplied = arguments.getlist(parameter)
        if len(supplied) > 1:
            raise InvalidAssessmentFilters("invalid_assessment_filters")
        values[parameter] = supplied[0] if supplied else default
    return normalize_filters(AssessmentFilters(
        search=values["q"],
        barangay=values["barangay"],
        programs=tuple(arguments.getlist("program")),
        completeness=values["completeness"],
        subsidy_mode=values["subsidy_mode"],
    ))
