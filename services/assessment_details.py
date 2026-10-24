"""Prepare explainable stored assessments and exact presentation contributions."""

from fractions import Fraction
from math import isclose

from services.assessment_filters import InvalidAssessmentFilters, parse_filter_arguments
from services.assessment_evidence_view import (
    DISPLAY_NAMES,
    prepare_evidence_view,
)
from services.assessment_explanations import explain_indicator
from services.assessment_queries import AssessmentQueryError, get_active_assessment_detail
from services.mpi_evidence import build_evidence_index, build_indicator_evidence
from services.mpi_indicator_content import INDICATORS
from services.prioritization import (
    PROGRAM_ORDER, _head_name, _household_reference_id, _source_text, priority_display,
)
from services.vulnerability.scoring import DIMENSIONS, INDICATOR_WEIGHTS
from services.subsidy_priority import derive_subsidy_priority
from services.subsidy_screening_content import STATUS_CLASSES, STATUS_LABELS
from services.subsidy_screening_details import prepare_subsidy_screening


INDICATOR_IDS = {
    "School Attendance": "mpi-school-attendance",
    "Educational Attainment": "mpi-educational-attainment",
    "Food Insecurity": "mpi-food-insecurity",
    "Illness/Treatment Proxy": "mpi-illness-treatment",
    "Sanitation": "mpi-sanitation",
    "Water": "mpi-water",
    "Assets": "mpi-assets",
    "Tenure": "mpi-tenure",
    "Housing Materials": "mpi-housing-materials",
    "Electricity": "mpi-electricity",
    "Internet": "mpi-internet",
    "Overcrowding": "mpi-overcrowding",
    "Public Transportation": "mpi-public-transportation",
    "Employment Proxy": "mpi-employment",
    "Underemployment Proxy": "mpi-underemployment",
    "Working Child Not in School": "mpi-working-child-not-in-school",
}


def detail_navigation(arguments):
    """Validate tab and list state; return only internally generated link parameters."""
    tab_values = arguments.getlist("tab")
    page_values = arguments.getlist("page")
    tab = tab_values[0] if tab_values else "overview"
    page = page_values[0] if page_values else "1"
    if (
        len(tab_values) > 1 or tab not in ("overview", "mpi", "screening", "reviews")
        or len(page_values) > 1 or not page.isascii() or not page.isdecimal()
        or len(page) > 9 or int(page) < 1
    ):
        raise InvalidAssessmentFilters("invalid_assessment_navigation")
    filters = parse_filter_arguments(arguments)
    return tab, {"page": int(page), **filters.parameters}


def _indicator(name, mpi, source, reference):
    """Explain one stored flag and its approved weight without deriving a new flag."""
    flag = mpi[name]
    if flag is not None and (type(flag) is not int or flag not in (0, 1)):
        raise AssessmentQueryError("invalid_stored_indicator")
    weight = INDICATOR_WEIGHTS[name]
    contribution = weight * 100 if flag == 1 else Fraction(0)
    spec = INDICATORS[name]
    source_evidence = build_indicator_evidence(name, source, reference)
    return {
        "name": name, "id": INDICATOR_IDS[name], "flag": flag,
        "display_name": DISPLAY_NAMES.get(name, name),
        "status": "Unknown" if flag is None else ("Deprived" if flag else "Not Deprived"),
        "status_class": "unknown" if flag is None else ("deprived" if flag else "not-deprived"),
        "explanation": explain_indicator(name, flag, source_evidence, reference),
        "rule": spec["rule"], "weight": str(weight), "weight_pct": float(weight * 100),
        "contribution": float(contribution), "exact_contribution": contribution,
        "evidence": prepare_evidence_view(name, source_evidence, source["context"]["survey_year"]),
    }


def prepare_assessment_detail(source, *, assessment_date=None):
    """Preserve stored metrics and flags; sum their approved weights for display.

    Reconciliation validates presentation only. It neither derives source flags
    nor assigns scores or ranks, and never calls an assessment engine.
    """
    mpi = source["findings"]["mpi"]
    reference = build_evidence_index(source)
    screening_programs = prepare_subsidy_screening(source, reference, assessment_date=assessment_date)
    statuses = {code: program["screening_status"]
                for code, program in source["findings"]["programs"].items()}
    statuses["SPISC"] = next(program["guidance"]["current_status"]
                             for program in screening_programs if program["code"] == "SPISC")
    dimensions = []
    total = Fraction(0)
    for dimension, (_, names) in DIMENSIONS.items():
        indicators = [_indicator(name, mpi, source, reference) for name in names]
        points = sum((item["exact_contribution"] for item in indicators), Fraction(0))
        total += points
        dimensions.append({
            "name": dimension, "points": float(points), "indicators": indicators,
        })
    if not isclose(float(total), mpi["vulnerability_score"], rel_tol=0, abs_tol=1e-8):
        raise AssessmentQueryError("stored_score_contribution_mismatch")
    identity = source["identity"]
    reviews = [dict(row) for row in source.get("staff_reviews", [])]
    for row in reviews:
        row.setdefault('effective_recommendation', row.get('closing_recommendation'))
        row.setdefault('effective_explanation', row.get('closing_explanation'))
        row.setdefault('effective_username', row.get('finished_username'))
        row.setdefault('effective_at', row.get('finished_at'))
        row.setdefault('withdrawn', False)
        row.setdefault('recommendation_version', 0)
        row.setdefault('recommendation_changes', [])
    for row in reviews:
        row['is_latest_review'] = row['current_review_id'] == row['id']
        row['can_change_recommendation'] = (
            row['status'] == 'Closed' and not row['withdrawn']
            and row['is_latest_review']
        )
    return {
        "review_history": reviews,
        "review_summary": [row for row in reviews if row['is_latest_review']],
        "context": source["context"], "household_uuid": source["findings"]["household_uuid"],
        "household_reference_id": _household_reference_id(identity["source_row_number"]),
        "head_name": _head_name(identity),
        "barangay": _source_text(identity["barangay"]) or "Not recorded",
        "mpi": mpi, "determined_count": 16 - mpi["unknown_indicator_count"],
        "priority_display": priority_display(derive_subsidy_priority(
            mpi["vulnerability_score"],
            statuses,
        )),
        "dimensions": dimensions,
        "unknown_indicators": [
            {"id": indicator["id"], "name": indicator["name"],
             "display_name": indicator["display_name"], "dimension": dimension["name"]}
            for dimension in dimensions for indicator in dimension["indicators"]
            if indicator["flag"] is None
        ],
        "programs": [
            {
                "name": code,
                "status": statuses[code],
                "status_label": STATUS_LABELS[statuses[code]],
                "status_class": STATUS_CLASSES[statuses[code]],
                "historical_status_label": STATUS_LABELS[
                    source["findings"]["programs"][code]["screening_status"]],
            }
            for code in PROGRAM_ORDER
        ],
        "screening_programs": screening_programs,
    }


def get_assessment_detail(household_uuid):
    """Prepare one protected detail response from existing read-only query context."""
    return prepare_assessment_detail(get_active_assessment_detail(
        household_uuid, include_staff_reviews=True,
    ))
