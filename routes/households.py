"""Searchable read-only household profiles from the development workspace."""

from flask import Blueprint, abort, render_template, request

from routes.main import get_workspace, paginate
from services.assessment_views import EDUCATION_FIELDS, EMPLOYMENT_FIELDS, field_values, listing_rows


households = Blueprint("households", __name__, url_prefix="/households")


@households.get("")
def index():
    workspace = get_workspace()
    query = request.args.get("q", "").strip()[:128]
    barangay = request.args.get("barangay", "")
    rows = listing_rows(workspace, query=query, barangay=barangay, sort="id")
    rows, paging = paginate(rows)
    return render_template("households/index.html", title="Household Records", rows=rows,
                           paging=paging, query=query, selected_barangay=barangay,
                           barangays=sorted({profile["barangay"] for profile in workspace["profiles"].values()}))


@households.get("/<uuid>")
def detail(uuid):
    workspace = get_workspace()
    profile = workspace["profiles"].get(uuid)
    if profile is None:
        abort(404)
    lookup = workspace["lookup"]
    members = []
    for row in profile["members"]:
        members.append({"name": row["display_name"], "line": row["line_number"],
                        "demographics": field_values(row, ("a02_relation_to_hh_head", "a05_sex", "a07_age"), lookup),
                        "education": field_values(row, EDUCATION_FIELDS, lookup),
                        "employment": field_values(row, EMPLOYMENT_FIELDS, lookup),
                        "health": field_values(row, ("section_l_line_number", "l07_currently_pregnant", "l16_pwd", "l34_has_been_sick", "l38_availed_medical_treatment"), lookup)})
    return render_template("households/detail.html", title="Household Profile", profile=profile, members=members)
