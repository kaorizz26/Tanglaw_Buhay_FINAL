"""Fixture-only public information pages with no household data access."""

from flask import Blueprint, abort, render_template

from routes.main import require_fixture_source
from services.public_programs import PUBLIC_PROGRAMS


public = Blueprint("public", __name__)


@public.get("/public")
def index():
    require_fixture_source()
    return render_template("public_landing.html", programs=PUBLIC_PROGRAMS)


@public.get("/public/programs/<program_slug>")
def program(program_slug):
    require_fixture_source()
    details = PUBLIC_PROGRAMS.get(program_slug)
    if details is None:
        abort(404)
    program_templates = {
        "4ps": "public_program_4ps.html",
        "spisc": "public_program_spisc.html",
        "slp": "public_program_slp.html",
        "tupad": "public_program_tupad.html",
    }
    return render_template(
        program_templates.get(program_slug, "public_program.html"),
        program=details,
        program_slug=program_slug,
        other_programs={
            slug: item for slug, item in PUBLIC_PROGRAMS.items()
            if slug != program_slug
        },
    )
