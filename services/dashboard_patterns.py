"""Present stored indicator counts for the Dashboard's Household Assessment Patterns.

Counts are read from the designated completed run elsewhere; nothing here
scores, ranks or reclassifies a finding.
"""

from services.assessment_evidence_view import DISPLAY_NAMES
from services.vulnerability.scoring import DIMENSIONS


AREA_KEYS = {
    "Education": "education",
    "Health and Nutrition": "health",
    "Housing, Water and Sanitation": "housing",
    "Employment": "employment",
}
DEFAULT_AREA = "education"
OUTCOMES = (
    ("deprived", "Deprived"),
    ("not_deprived", "Not Deprived"),
    ("unknown", "Could Not Determine"),
)


def select_area(value):
    """Return a valid area key; anything else falls back to the default."""
    return value if value in AREA_KEYS.values() else DEFAULT_AREA


def _percent(count, total):
    return round(count / total * 100, 1) if total else 0


def present_indicator_patterns(raw, households):
    """Group counts by the existing four areas, requiring every indicator to reconcile.

    Raises ValueError for a count that is not a plain integer or an indicator
    whose three outcomes do not add up to the assessed households.
    """
    if raw["assessed_households"] != households:
        raise ValueError("indicator_household_mismatch")
    areas = []
    for area, (_, names) in DIMENSIONS.items():
        indicators = []
        for name in names:
            counts = raw["indicators"][name]
            if any(type(counts[key]) is not int for key, _ in OUTCOMES):
                raise ValueError("indicator_count_invalid")
            if sum(counts[key] for key, _ in OUTCOMES) != households:
                raise ValueError("indicator_count_mismatch")
            indicators.append({
                "name": DISPLAY_NAMES.get(name, name),
                "outcomes": [
                    {"key": key, "label": label, "count": counts[key],
                     "percent": _percent(counts[key], households)}
                    for key, label in OUTCOMES
                ],
            })
        areas.append({"key": AREA_KEYS[area], "name": area, "indicators": indicators})
    return areas
