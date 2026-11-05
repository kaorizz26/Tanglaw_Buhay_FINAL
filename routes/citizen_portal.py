"""Citizen Portal pages: activation, sign-in, home, Change Password and sign-out.

These routes never use the LGU Staff session (g.user). Citizens have their own
cookie and the helpers in services/citizen_sessions.py.
"""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
import psycopg

from services import citizen_sessions as sessions
from services.public_programs import PUBLIC_PROGRAMS
from services.citizen_authentication import (
    ActivationLinkError, CitizenFormError, PortalUnavailable, SignInError,
    activate_account, activation_link_is_usable, change_password as change_citizen_password,
    sign_in,
)

citizen_portal = Blueprint("citizen_portal", __name__, url_prefix="/portal")

SIGN_IN_FAILED = "We could not sign you in. Check your email address and password, then try again."
LOCKED_OUT = (
    "Too many incorrect passwords. For your safety your account is locked for 15 minutes "
    "and you have been signed out."
)
UNAVAILABLE = "The Citizen Portal is unavailable right now. Please try again later."


@citizen_portal.before_request
def prepare_citizen_session():
    """Keep the portal closed if the signing secret is missing, then load the session."""
    if not sessions.sessions_configured():
        abort(503, description=UNAVAILABLE)
    sessions.load_session()


@citizen_portal.after_request
def finish_citizen_response(response):
    """Write the session cookie if it changed, and keep activation pages out of referrers."""
    response.headers["Referrer-Policy"] = "no-referrer"
    return sessions.save_cookie(response)


@citizen_portal.errorhandler(400)
@citizen_portal.errorhandler(503)
def portal_error(error):
    return render_template("citizen_portal/error.html", message=error.description), error.code


def _render(template, status=200, **context):
    return render_template(
        f"citizen_portal/{template}", csrf_token=sessions.csrf_token(), **context,
    ), status


def _link_problem_page(error):
    """Explain a link that cannot be used and what to do next."""
    return _render("activation_problem.html", 410, cancelled=error.reason == "cancelled")


# ---- Activation -----------------------------------------------------------------

@citizen_portal.route("/activate/<token>", methods=["GET", "POST"])
def activate(token):
    """Let the representative enter the code from LGU Staff and choose a password."""
    try:
        if request.method == "GET":
            if not activation_link_is_usable(token):
                return _link_problem_page(ActivationLinkError("invalid"))
            return _render("activate.html", token=token, error=None)
        sessions.check_csrf()
        activate_account(
            token, request.form.get("code", ""), request.form.get("new_password", ""),
            request.form.get("confirm_password", ""),
        )
    except ActivationLinkError as problem:
        return _link_problem_page(problem)
    except CitizenFormError as problem:
        return _render("activate.html", 400, token=token, error=str(problem))
    except (PortalUnavailable, RuntimeError):
        abort(503, description=UNAVAILABLE)
    return redirect(url_for("citizen_portal.login", activated=1), code=303)


# ---- Sign-in, home, sign-out ----------------------------------------------------

@citizen_portal.route("/login", methods=["GET", "POST"])
def login():
    """Sign in with email address and password."""
    if g.citizen is not None:
        return redirect(url_for("citizen_portal.home"))
    error = None
    if request.method == "POST":
        sessions.check_csrf()
        try:
            account = sign_in(request.form.get("email", ""), request.form.get("password", ""))
        except SignInError:
            error = SIGN_IN_FAILED
        except PortalUnavailable:
            abort(503, description=UNAVAILABLE)
        else:
            sessions.begin_session(account)
            return redirect(url_for("citizen_portal.home"), code=303)
    return _render(
        "login.html", 401 if error else 200, error=error,
        activated=request.args.get("activated") == "1", email=request.form.get("email", ""),
    )


@citizen_portal.get("/")
@sessions.citizen_required
def home():
    """Welcome page: the account holder's own name and email, and links to the public
    program information pages. No household assessment or eligibility is shown."""
    return _render("home.html", account=g.citizen, programs=PUBLIC_PROGRAMS)


@citizen_portal.route("/change-password", methods=["GET", "POST"])
@sessions.citizen_required
def change_password():
    """Change the password; the citizen stays signed in on this device only."""
    error = None
    if request.method == "POST":
        sessions.check_csrf()
        try:
            updated = change_citizen_password(
                g.citizen, request.form.get("current_password", ""),
                request.form.get("new_password", ""), request.form.get("confirm_password", ""),
            )
        except CitizenFormError as problem:
            error = str(problem)
        except SignInError:
            sessions.end_session(g.citizen["id"])
            return _render("login.html", 401, error=LOCKED_OUT, activated=False, email="")
        except PortalUnavailable:
            abort(503, description=UNAVAILABLE)
        else:
            sessions.begin_session(updated)
            return _render("change_password.html", account=updated, error=None, changed=True)
    return _render(
        "change_password.html", 400 if error else 200, account=g.citizen, error=error,
        changed=False,
    )


@citizen_portal.post("/logout")
def logout():
    """Sign out on every device."""
    sessions.check_csrf()
    if g.citizen is not None:
        try:
            sessions.end_session(g.citizen["id"])
        except (psycopg.Error, RuntimeError):
            abort(503, description=UNAVAILABLE)
    return redirect(url_for("citizen_portal.login"), code=303)
