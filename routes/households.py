"""Authenticated read-only household source records."""

from flask import Blueprint, abort, g, render_template, request, url_for

from services.assessment_queries import AssessmentQueryError
from services.auth import ADMIN_ROLE, csrf_token
from services.household_records import (
    InvalidRecordFilters, get_record_detail, get_record_page, parse_record_arguments,
)


households = Blueprint("households", __name__)


def _staff_context():
    return {
        "role_label": "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff",
        "staff_username": g.user["username"], "csrf_token": csrf_token(),
    }


@households.get("/households")
def index():
    try:
        page, filters = parse_record_arguments(request.args)
        results = get_record_page(page, filters)
    except InvalidRecordFilters:
        abort(400, description="Choose a valid household search or barangay.")
    except AssessmentQueryError:
        abort(503, description="Household records are unavailable. Please try again later.")
    if results is None:
        abort(404, description="This household records page does not exist.")
    return render_template(
        "households/index.html", results=results, **_staff_context(),
    )


@households.get("/households/<uuid:household_uuid>")
def detail(household_uuid):
    try:
        page, filters = parse_record_arguments(request.args)
        record = get_record_detail(str(household_uuid))
    except InvalidRecordFilters:
        abort(400, description="Choose a valid household return link.")
    except AssessmentQueryError as failure:
        if str(failure) == "household_record_missing":
            abort(404, description="This household record is unavailable in the selected batch.")
        abort(503, description="Household records are unavailable. Please try again later.")
    return render_template(
        "households/detail.html", record=record,
        list_url=url_for("households.index", page=page, **filters.parameters),
        **_staff_context(),
    )
