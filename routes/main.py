"""Minimal protected landing, retired staff URLs and an independent health check."""

from flask import Blueprint, abort, current_app, g, jsonify, render_template

from services.auth import ADMIN_ROLE, csrf_token

main = Blueprint("main", __name__)


def require_fixture_source():
    """Keep the development-only source boundary even though no records are rendered."""
    if current_app.extensions["development_workspace"].get("source_kind") != "development_fixture":
        abort(403)


@main.get("/")
def index():
    require_fixture_source()
    role_label = "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff"
    return render_template("landing.html", role_label=role_label, csrf_token=csrf_token())


@main.get("/households")
@main.get("/households/<path:retired_path>")
@main.get("/assessments")
@main.get("/assessments/<path:retired_path>")
def retired_staff_pages(retired_path=None):
    """Retire old bookmarks without looking up or echoing any household information."""
    abort(410, description="This staff page has been retired while the interface is rebuilt.")


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
