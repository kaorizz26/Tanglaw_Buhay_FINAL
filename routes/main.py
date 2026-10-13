"""Landing/health routes and shared access to the fixture-only UI workspace."""

from math import ceil

from flask import Blueprint, abort, current_app, jsonify, redirect, request, url_for

main = Blueprint("main", __name__)


def get_workspace():
    workspace = current_app.extensions["development_workspace"]
    if workspace["source_kind"] != "development_fixture":
        abort(403)
    return workspace


def paginate(rows, *, per_page=10):
    """Pagination retains every filter in next/previous/page links."""
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        abort(400, description="Page must be a positive integer.")
    if page < 1:
        abort(400, description="Page must be a positive integer.")
    pages = max(1, ceil(len(rows) / per_page))
    page = min(page, pages)
    start = (page - 1) * per_page

    def page_url(number):
        arguments = request.args.to_dict()
        arguments["page"] = number
        return url_for(request.endpoint, **arguments)

    return rows[start:start + per_page], {
        "page": page, "pages": pages, "total": len(rows),
        "start": start + 1 if rows else 0, "end": min(start + per_page, len(rows)),
        "previous": page_url(page - 1) if page > 1 else None,
        "next": page_url(page + 1) if page < pages else None,
        "links": [(number, page_url(number)) for number in range(1, pages + 1)],
    }


@main.app_context_processor
def workspace_context():
    return {"workspace": get_workspace()}


@main.get("/")
def index():
    return redirect(url_for("assessments.index"))


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
