"""Prepare explainable stored assessments and exact presentation contributions."""

from fractions import Fraction
from math import isclose

from services.assessment_filters import InvalidAssessmentFilters, parse_filter_arguments
from services.assessment_evidence_view import (
    STAFF_EXPLANATIONS,
    direct_condition,
    prepare_evidence_view,
)
from services.assessment_queries import AssessmentQueryError, get_active_assessment_detail
from services.mpi_evidence import build_evidence_index, build_indicator_evidence
from services.mpi_indicator_content import INDICATORS
from services.prioritization import PROGRAM_ORDER, _head_name, _household_reference_id, _source_text
from services.vulnerability.scoring import DIMENSIONS, INDICATOR_WEIGHTS


def detail_navigation(arguments):
    """Validate tab and list state; return only internally generated link parameters."""
    tab_values = arguments.getlist("tab")
    page_values = arguments.getlist("page")
    tab = tab_values[0] if tab_values else "overview"
    page = page_values[0] if page_values else "1"
    if (
        len(tab_values) > 1 or tab not in ("overview", "mpi")
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
        "name": name, "flag": flag,
        "status": "Unknown" if flag is None else ("Deprived" if flag else "Not Deprived"),
        "status_class": "unknown" if flag is None else ("deprived" if flag else "not-deprived"),
        "explanation": direct_condition(name, flag, source_evidence)
        or STAFF_EXPLANATIONS[name][2 if flag is None else flag],
        "rule": spec["rule"], "weight": str(weight), "weight_pct": float(weight * 100),
        "contribution": float(contribution), "exact_contribution": contribution,
        "evidence": prepare_evidence_view(name, source_evidence),
    }


def prepare_assessment_detail(source):
    """Preserve stored metrics and flags; sum their approved weights for display.

    Reconciliation validates presentation only. It neither derives source flags
    nor assigns scores or ranks, and never calls an assessment engine.
    """
    mpi = source["findings"]["mpi"]
    reference = build_evidence_index(source)
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
    return {
        "context": source["context"], "household_uuid": source["findings"]["household_uuid"],
        "household_reference_id": _household_reference_id(identity["source_row_number"]),
        "head_name": _head_name(identity),
        "barangay": _source_text(identity["barangay"]) or "Not recorded",
        "mpi": mpi, "determined_count": 16 - mpi["unknown_indicator_count"],
        "dimensions": dimensions,
        "programs": [{"name": code, "status": source["findings"]["programs"][code]["screening_status"]}
                     for code in PROGRAM_ORDER],
    }


def get_assessment_detail(household_uuid):
    """Prepare one protected detail response from existing read-only query context."""
    return prepare_assessment_detail(get_active_assessment_detail(household_uuid))
