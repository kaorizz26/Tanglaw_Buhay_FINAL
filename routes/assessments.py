"""Authenticated HTTP presentation of the explicitly selected stored assessment."""

from flask import Blueprint, abort, g, render_template, request

from services.assessment_queries import AssessmentQueryError
from services.assessment_filters import InvalidAssessmentFilters, parse_filter_arguments
from services.auth import ADMIN_ROLE, csrf_token
from services.prioritization import get_prioritization_page
from services.assessment_details import detail_navigation, get_assessment_detail


assessments = Blueprint("assessments", __name__)


@assessments.get("/assessments")
def prioritization():
    """Render one stored summary page after the application's staff authorization."""
    if "view" in request.args:
        abort(
            410,
            description="This assessment view is not available while the interface is rebuilt.",
        )
    page_values = request.args.getlist("page")
    raw_page = page_values[0] if page_values else "1"
    if len(page_values) > 1 or not raw_page.isascii() or not raw_page.isdecimal():
        abort(400, description="Choose a valid page number.")
    if len(raw_page) > 9 or int(raw_page) < 1:
        abort(400, description="Choose a valid page number.")
    try:
        filters = parse_filter_arguments(request.args)
        results = get_prioritization_page(int(raw_page), filters)
    except InvalidAssessmentFilters:
        abort(400, description="Choose valid search and filter values.")
    except AssessmentQueryError:
        abort(503, description="Household assessments are unavailable. Please try again later.")
    if results is None:
        abort(404, description="This assessment page does not exist.")
    role_label = "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff"
    return render_template(
        "assessments/prioritization.html",
        results=results,
        role_label=role_label,
        staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@assessments.get("/assessments/<uuid:household_uuid>")
def detail(household_uuid):
    """Show one stored household only after the unchanged staff authorization guard."""
    try:
        tab, list_parameters = detail_navigation(request.args)
        results = get_assessment_detail(str(household_uuid))
    except InvalidAssessmentFilters:
        abort(400, description="Choose a valid assessment section and return filters.")
    except AssessmentQueryError as error:
        if str(error) == "household_assessment_missing":
            abort(404, description="This household assessment is unavailable in the selected batch.")
        abort(503, description="Household assessments are unavailable. Please try again later.")
    role_label = "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff"
    return render_template(
        "assessments/detail.html", results=results, tab=tab,
        list_parameters=list_parameters, role_label=role_label,
        staff_username=g.user["username"], csrf_token=csrf_token(),
    )
