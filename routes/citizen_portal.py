"""Citizen Portal pages: activation, sign-in, home, Change Password and sign-out.

These routes never use the LGU Staff session (g.user). Citizens have their own
cookie and the helpers in services/citizen_sessions.py.
"""

from typing import NamedTuple

from flask import (
    Blueprint, abort, current_app, g, make_response, redirect, render_template, request, url_for,
)
from itsdangerous import BadSignature, URLSafeTimedSerializer
import psycopg

from services import citizen_sessions as sessions
from services.citizen_password_reset import (
    ResetLinkError, request_password_reset, reset_link_is_usable, reset_password,
)
from services.citizen_documents import DocumentAccessError, count_needing_action, list_my_documents
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


class Handoff(NamedTuple):
    """Where one kind of emailed link keeps its token after the redirect to a clean address."""

    cookie_name: str
    path: str
    salt: str


# An emailed link's token is kept in a signed, short-lived cookie so it does not stay in the
# address bar. Each kind of link has its own cookie, path and signing salt, so one can never
# be used in place of the other.
ACTIVATION_HANDOFF = Handoff(
    "tanglaw_citizen_activation", "/portal/activate", "tanglaw-citizen-activation-handoff",
)
RESET_HANDOFF = Handoff(
    "tanglaw_citizen_reset", "/portal/reset", "tanglaw-citizen-reset-handoff",
)
HANDOFF_LIFETIME_SECONDS = 15 * 60


@citizen_portal.before_request
def prepare_citizen_session():
    """Keep the portal closed if the signing secret is missing, then load the session."""
    if not sessions.sessions_configured():
        abort(503, description=UNAVAILABLE)
    sessions.load_session()


@citizen_portal.after_request
def finish_citizen_response(response):
    """Write the session cookie if it changed, and keep link pages out of referrers and caches."""
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith((ACTIVATION_HANDOFF.path, RESET_HANDOFF.path)):
        response.headers["Cache-Control"] = "no-store"
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

def _handoff_serializer(handoff):
    """Signs the handoff with the app's secret key and the link kind's own purpose salt."""
    return URLSafeTimedSerializer(current_app.secret_key, salt=handoff.salt)


def _handoff_cookie_settings(handoff):
    """Cookie options shared by setting and clearing the handoff cookie."""
    return {
        "path": handoff.path, "httponly": True, "samesite": "Lax",
        "secure": current_app.config["SESSION_COOKIE_SECURE"],
    }


def _save_handoff(response, handoff, token):
    """Keep the emailed link's token in a short-lived signed cookie on this response."""
    response.set_cookie(
        handoff.cookie_name, _handoff_serializer(handoff).dumps(token),
        max_age=HANDOFF_LIFETIME_SECONDS, **_handoff_cookie_settings(handoff),
    )
    return response


def _clear_handoff(response, handoff):
    """Remove the saved handoff from the browser."""
    response.delete_cookie(handoff.cookie_name, **_handoff_cookie_settings(handoff))
    return response


def _read_handoff_token(handoff):
    """Return the link token saved by the emailed link, or None if missing, forged or expired."""
    value = request.cookies.get(handoff.cookie_name)
    if not value:
        return None
    try:
        token = _handoff_serializer(handoff).loads(value, max_age=HANDOFF_LIFETIME_SECONDS)
    except BadSignature:  # Also covers expired signatures.
        return None
    return token if isinstance(token, str) else None


def _handoff_ended_page():
    """Tell the citizen to reopen the emailed link, since the saved handoff is gone."""
    response = make_response(_render("activation_problem.html", 410, handoff_ended=True))
    return _clear_handoff(response, ACTIVATION_HANDOFF)


def _link_problem_response(problem):
    """The 'link cannot be used' page, also removing any saved handoff."""
    page, status = _link_problem_page(problem)
    return _clear_handoff(make_response(page, status), ACTIVATION_HANDOFF)


@citizen_portal.route("/activate/<token>")
def open_activation_link(token):
    """Check the emailed link, keep its token in a short-lived cookie and move to a clean address."""
    try:
        if not activation_link_is_usable(token):
            return _link_problem_response(ActivationLinkError("invalid"))
    except (PortalUnavailable, RuntimeError):
        abort(503, description=UNAVAILABLE)
    response = redirect(url_for("citizen_portal.activate"), code=303)
    return _save_handoff(response, ACTIVATION_HANDOFF, token)


@citizen_portal.route("/activate", methods=["GET", "POST"])
def activate():
    """Let the representative enter the code from LGU Staff and choose a password."""
    token = _read_handoff_token(ACTIVATION_HANDOFF)
    if token is None:
        return _handoff_ended_page()
    try:
        if request.method == "GET":
            if not activation_link_is_usable(token):
                return _link_problem_response(ActivationLinkError("invalid"))
            return _render("activate.html", error=None)
        sessions.check_csrf()
        activate_account(
            token, request.form.get("code", ""), request.form.get("new_password", ""),
            request.form.get("confirm_password", ""),
        )
    except ActivationLinkError as problem:
        return _link_problem_response(problem)
    except CitizenFormError as problem:
        return _render("activate.html", 400, error=str(problem))
    except (PortalUnavailable, RuntimeError):
        abort(503, description=UNAVAILABLE)
    response = redirect(url_for("citizen_portal.login", activated=1), code=303)
    return _clear_handoff(response, ACTIVATION_HANDOFF)


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
        activated=request.args.get("activated") == "1",
        password_reset=request.args.get("reset") == "1", email=request.form.get("email", ""),
    )


# ---- Forgot password ------------------------------------------------------------

@citizen_portal.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Ask for a reset link. The answer is the same whether or not the email has an account."""
    if g.citizen is not None:
        return redirect(url_for("citizen_portal.change_password"))
    if request.method == "POST":
        sessions.check_csrf()
        try:
            request_password_reset(request.form.get("email", ""))
        except PortalUnavailable:
            abort(503, description=UNAVAILABLE)
        # Redirect so that refreshing the page does not send another request.
        return redirect(url_for("citizen_portal.forgot_password", sent=1), code=303)
    if request.args.get("sent") == "1":
        return _render("forgot_password_sent.html")
    return _render("forgot_password.html")


def _reset_problem_response():
    """The 'reset link cannot be used' page, also removing any saved handoff."""
    return _clear_handoff(make_response(_render("reset_problem.html", 410)), RESET_HANDOFF)


@citizen_portal.route("/reset/<token>")
def open_reset_link(token):
    """Check the emailed link, keep its token in a short-lived cookie and move to a clean address.

    Looking at the link never uses it up; only saving a new password does.
    """
    try:
        if not reset_link_is_usable(token):
            return _reset_problem_response()
    except PortalUnavailable:
        abort(503, description=UNAVAILABLE)
    response = redirect(url_for("citizen_portal.reset"), code=303)
    return _save_handoff(response, RESET_HANDOFF, token)


@citizen_portal.route("/reset", methods=["GET", "POST"])
def reset():
    """Choose a new password. Only the POST uses the link up."""
    token = _read_handoff_token(RESET_HANDOFF)
    if token is None:
        return _reset_problem_response()
    try:
        if request.method == "GET":
            if not reset_link_is_usable(token):
                raise ResetLinkError
            return _render("reset_password.html", error=None)
        sessions.check_csrf()
        reset_password(
            token, request.form.get("new_password", ""), request.form.get("confirm_password", ""),
        )
    except ResetLinkError:
        return _reset_problem_response()
    except CitizenFormError as problem:
        return _render("reset_password.html", 400, error=str(problem))
    except PortalUnavailable:
        abort(503, description=UNAVAILABLE)
    response = redirect(url_for("citizen_portal.login", reset=1), code=303)
    return _clear_handoff(response, RESET_HANDOFF)


def _documents_to_upload(account_id):
    """Count the documents waiting for an upload; None hides the count if it cannot be read."""
    try:
        return count_needing_action(list_my_documents(account_id))
    except DocumentAccessError:
        return None


@citizen_portal.get("/")
@sessions.citizen_required
def home():
    """Welcome page: the account holder's own name and email, and links to the public
    program information pages. No household assessment or eligibility is shown."""
    return _render(
        "home.html", account=g.citizen, programs=PUBLIC_PROGRAMS,
        documents_to_upload=_documents_to_upload(g.citizen["id"]),
    )


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
