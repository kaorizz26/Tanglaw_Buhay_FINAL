"""Flask entry point for Tanglaw-Buhay."""

from flask import Flask, render_template

from config import Config
from routes.main import main
from routes.households import households
from routes.assessments import assessments
from services.development_data import build_development_workspace


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

    @app.after_request
    def prevent_record_caching(response):
        if not response.headers.get("Cache-Control"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(404)
    def request_error(error):
        return render_template("error.html", title="Page unavailable", error=error), error.code

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", debug=False)
