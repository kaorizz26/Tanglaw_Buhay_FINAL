"""Flask entry point for Tanglaw-Buhay."""

from flask import Flask, g, render_template, request

from config import Config
from commands import register_commands
from routes.auth import auth
from routes.main import main
from routes.households import households
from routes.assessments import assessments
from services.development_data import build_development_workspace
from services.auth import protect_staff_routes


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    # Public development routes have no switch to load actual CBMS data.
    # The engines run once at startup, not on listing/detail requests.
    app.extensions["development_workspace"] = build_development_workspace()
    app.register_blueprint(main)
    app.register_blueprint(households)
    app.register_blueprint(assessments)
    app.register_blueprint(auth)
    app.before_request(protect_staff_routes)
    register_commands(app)

    @app.after_request
    def prevent_record_caching(response):
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(503)
    def request_error(error):
        if (error.code == 503 or request.blueprint == "auth"
                or getattr(g, "user", None) is None):
            return render_template("auth/error.html", error=error), error.code
        return render_template("error.html", title="Page unavailable", error=error), error.code

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False)
