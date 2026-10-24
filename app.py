"""Flask entry point for Tanglaw-Buhay."""

from zoneinfo import ZoneInfo

from flask import Flask, render_template

from config import Config
from commands import register_commands
from routes.assessments import assessments
from routes.auth import auth
from routes.main import main
from services.auth import protect_staff_routes
from services.assessment_request_logging import protect_assessment_access_logs


PHILIPPINE_TIME = ZoneInfo("Asia/Manila")


def _philippine_time(value, pattern):
    """Format a stored timezone-aware value in Philippine Time without changing it."""
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime; timezone required")
    return value.astimezone(PHILIPPINE_TIME).strftime(pattern)


def pht_datetime(value):
    return _philippine_time(value, "%d %b %Y, %I:%M %p PHT")


def pht_date(value):
    return _philippine_time(value, "%d %b %Y")


def create_app(test_config=None):
    app = Flask(__name__)
    protect_assessment_access_logs()
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    # Retain the fixture boundary without loading records or running engines for UI.
    # No configuration switch enables actual CBMS access.
    app.extensions["development_workspace"] = {"source_kind": "development_fixture"}
    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(assessments)
    app.before_request(protect_staff_routes)
    register_commands(app)
    app.add_template_filter(pht_datetime)
    app.add_template_filter(pht_date)

    @app.after_request
    def prevent_record_caching(response):
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(410)
    @app.errorhandler(503)
    def request_error(error):
        return render_template("auth/error.html", error=error), error.code

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False)
