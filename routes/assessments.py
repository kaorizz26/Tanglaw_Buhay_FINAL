"""Prioritization and detail views of precomputed, unchanged assessments."""

from flask import Blueprint, abort, render_template, request

from routes.main import get_workspace, paginate
from services.assessment_views import assessment_details, listing_rows
from services.subsidy_screening import PROGRAMS


assessments = Blueprint("assessments", __name__, url_prefix="/assessments")


@assessments.get("")
def index():
    workspace = get_workspace()
    view = request.args.get("view", "prioritization")
    if view not in ("prioritization", "queue"):
        abort(400, description="Unknown assessment view.")
    query = request.args.get("q", "").strip()[:128]
    barangay = request.args.get("barangay", "")
    program = request.args.get("program", "")
    quality = request.args.get("quality", "")
    sort = request.args.get("sort", "rank")
    if program not in ("", *PROGRAMS) or quality not in ("", "complete", "needs_review") or sort not in ("rank", "score_asc", "coverage", "id"):
        abort(400, description="Unknown assessment filter or sort option.")
    rows = listing_rows(workspace, query=query, barangay=barangay, program=program, quality=quality, sort=sort)
    rows, paging = paginate(rows)
    all_results = list(workspace["results"].values())
    summary = {
        "total": len(all_results),
        "complete": sum(row["mpi"]["unknown_indicator_count"] == 0 for row in all_results),
        "unknown": sum(row["mpi"]["unknown_indicator_count"] > 0 for row in all_results),
        "flagged": sum(any(program["screening_status"] == "For Review" for program in row["programs"].values()) for row in all_results),
    }
    return render_template("assessments/index.html", title="Assessments", view=view,
                           rows=rows, paging=paging, summary=summary, query=query,
                           selected_barangay=barangay, selected_program=program,
                           selected_quality=quality, selected_sort=sort, programs=PROGRAMS,
                           barangays=sorted({profile["barangay"] for profile in workspace["profiles"].values()}))


@assessments.get("/<uuid>")
def detail(uuid):
    workspace = get_workspace()
    result = workspace["results"].get(uuid)
    if result is None:
        abort(404)
    tab = request.args.get("tab", "overview")
    program = request.args.get("program", "4Ps")
    if tab not in ("overview", "mpi", "subsidy", "history") or program not in PROGRAMS:
        abort(400, description="Unknown assessment section or program.")
    profile = workspace["profiles"][uuid]
    details = assessment_details(result, profile, workspace["lookup"])
    return render_template("assessments/detail.html", title="Household Assessment", profile=profile,
                           result=result, details=details, tab=tab, selected_program=program)
