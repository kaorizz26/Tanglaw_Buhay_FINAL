"""Flask entry point for Tanglaw-Buhay."""

from flask import Flask, render_template

from config import Config
from commands import register_commands
from routes.assessments import assessments
from routes.auth import auth
from routes.main import main
from services.auth import protect_staff_routes
from services.assessment_request_logging import protect_assessment_access_logs


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

    @app.after_request
    def prevent_record_caching(response):
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(404)
    @app.errorhandler(410)
    @app.errorhandler(503)
    def request_error(error):
        return render_template("auth/error.html", error=error), error.code

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False)
