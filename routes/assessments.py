"""Authenticated HTTP presentation of the explicitly selected stored assessment."""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from services.assessment_queries import AssessmentQueryError
from services.assessment_filters import InvalidAssessmentFilters, parse_filter_arguments
from services.auth import ADMIN_ROLE, check_csrf, csrf_token
from services.prioritization import get_prioritization_page
from services.assessment_details import detail_navigation, get_assessment_detail
from services.staff_reviews import (
    StaffReviewError, get_staff_review_detail, start_staff_review,
)
from services.subsidy_screening_content import PROGRAM_NAMES


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
        if tab == "reviews":
            results = get_staff_review_detail(str(household_uuid))
        else:
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


@assessments.route(
    "/assessments/<uuid:household_uuid>/reviews/<program>", methods=["GET", "POST"],
)
def start_review(household_uuid, program):
    """Confirm and explicitly initiate a review using the authenticated staff ID."""
    if request.method == "POST":
        check_csrf()
    if program not in PROGRAM_NAMES:
        abort(404, description="Choose one of the available subsidy programs.")
    try:
        _, list_parameters = detail_navigation(request.args)
    except InvalidAssessmentFilters:
        abort(400, description="Choose valid return filters.")
    error = None
    reason = ""
    if request.method == "POST":
        try:
            for key in ("batch_id", "run_id", "starting_reason"):
                if len(request.form.getlist(key)) > 1:
                    raise StaffReviewError("invalid_request")
            context_ids = []
            for key in ("batch_id", "run_id"):
                value = request.form.get(key, "")
                if not value.isascii() or not value.isdecimal() or len(value) > 18:
                    raise StaffReviewError("invalid_request")
                context_ids.append(int(value))
            reason = request.form.get("starting_reason", "")
            start_staff_review(
                str(household_uuid), program, reason, g.user["id"], *context_ids,
            )
        except StaffReviewError as failure:
            code = str(failure)
            if code == "already_active":
                flash("A review is already in progress for this program.")
            elif code in ("reason_required", "invalid_reason"):
                error = (
                    "Enter a brief reason for starting this review."
                    if code == "reason_required"
                    else "Use a reason of no more than 1,000 characters."
                )
            elif code == "unauthorized":
                abort(403, description="Your account cannot start this review.")
            elif code == "household_missing":
                abort(404, description=(
                    "This household assessment is unavailable in the selected batch."
                ))
            elif code in ("invalid_request", "context_changed"):
                abort(400, description=(
                    "The household selection changed or the form expired. "
                    "Return to Staff Reviews and try again."
                ))
            else:
                abort(503, description=(
                    "The review could not be saved. Return to Staff Reviews "
                    "to check its status before trying again."
                ))
        else:
            flash(f"The {program} staff review has started.")
        if error is None:
            return redirect(url_for(
                "assessments.detail", household_uuid=household_uuid, tab="reviews",
                **list_parameters,
            ), code=303)
    try:
        results = get_staff_review_detail(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) == "household_assessment_missing":
            abort(404, description=(
                "This household assessment is unavailable in the selected batch."
            ))
        abort(503, description="Staff reviews are unavailable. Please try again later.")
    selected = next(
        item for item in results["staff_reviews"] if item["name"] == program
    )
    if selected["review"]:
        return redirect(url_for(
            "assessments.detail", household_uuid=household_uuid, tab="reviews",
            **list_parameters,
        ), code=303)
    return render_template(
        "assessments/detail.html", results=results, tab="reviews", start_program=selected,
        review_error=error, starting_reason=reason[:1000], list_parameters=list_parameters,
        role_label="System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff",
        staff_username=g.user["username"], csrf_token=csrf_token(),
    ), (400 if error else 200)
