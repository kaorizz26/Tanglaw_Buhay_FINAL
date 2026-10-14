"""Authenticated HTTP presentation of the explicitly selected stored assessment."""

from flask import Blueprint, abort, g, render_template, request

from services.assessment_queries import AssessmentQueryError
from services.auth import ADMIN_ROLE, csrf_token
from services.prioritization import get_prioritization_page


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
        results = get_prioritization_page(int(raw_page))
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
