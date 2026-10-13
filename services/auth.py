"""Credential verification and database-backed session authorization."""

from functools import wraps
import re
from secrets import compare_digest, token_urlsafe
from time import time

from flask import abort, current_app, g, redirect, request, session, url_for
import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import staff_users

ADMIN_ROLE = "system_administrator"
STAFF_ROLE = "lgu_staff"
# Unknown usernames still perform the same expensive password verification.
_DUMMY_HASH = generate_password_hash(token_urlsafe(32), method="scrypt")


def authentication_configured():
    """Fail closed for staff access while allowing the independent health check."""
    key = current_app.secret_key
    return isinstance(key, (str, bytes)) and len(key) >= 32


def authenticate(username, password):
    """Return a valid active account or None; never return its password hash."""
    with get_connection() as connection:
        user = staff_users.find_by_username(connection, username.strip().lower())
    valid = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not valid or not user or not user["is_active"]:
        return None
    return {key: value for key, value in user.items() if key != "password_hash"}


def start_session(user):
    """Replace pre-login state and issue a fixed-duration authenticated session."""
    session.clear()
    session.update(user_id=user["id"], session_version=user["session_version"],
                   issued_at=int(time()), csrf_token=token_urlsafe(32))
    session.permanent = True


def end_session():
    """Revoke server-side sessions before clearing the browser cookie."""
    with get_connection() as connection:
        staff_users.invalidate_sessions(connection, g.user["id"])
    session.clear()


def csrf_token():
    """Create an unpredictable token bound to this browser's signed session."""
    if "csrf_token" not in session:
        session["csrf_token"] = token_urlsafe(32)
    return session["csrf_token"]


def check_csrf():
    """Reject missing or mismatched form tokens before processing credentials/actions."""
    expected = session.get("csrf_token")
    supplied = request.form.get("csrf_token", "")
    if (not isinstance(expected, str)
            or not compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))):
        abort(400, description="The form expired. Reload and try again.")


def load_session_user():
    """Recheck activity, role and revocation state on every authenticated request."""
    g.user = None
    user_id = session.get("user_id")
    issued = session.get("issued_at")
    lifetime = current_app.permanent_session_lifetime.total_seconds()
    if user_id is None:
        return
    if (type(user_id) is not int or type(issued) is not int
            or not 0 <= time() - issued < lifetime):
        session.clear()
        return
    with get_connection() as connection:
        user = staff_users.find_by_id(connection, user_id)
    if (not user or not user["is_active"]
            or user["role"] not in (ADMIN_ROLE, STAFF_ROLE)
            or user["session_version"] != session.get("session_version")):
        session.clear()
        return
    g.user = user


def protect_staff_routes():
    """Default-deny application routes; only login, static files and health are public."""
    if request.endpoint in (None, "static", "main.test_route"):
        return None
    if not authentication_configured():
        abort(503, description="Staff authentication is unavailable.")
    try:
        load_session_user()
    except (psycopg.Error, RuntimeError):
        abort(503, description="Staff authentication is unavailable.")
    if request.endpoint != "auth.login" and g.user is None:
        return redirect(url_for("auth.login"))
    return None


def admin_required(view):
    """Reusable server-side guard for future import/account routes."""

    @wraps(view)
    def guarded(*args, **kwargs):
        if getattr(g, "user", None) is None:
            return redirect(url_for("auth.login"))
        if g.user["role"] != ADMIN_ROLE:
            abort(403)
        return view(*args, **kwargs)
    return guarded


def create_initial_admin(username, password):
    """Validate bootstrap inputs and transactionally store only a salted hash."""
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
        raise ValueError("Use 3-64 username characters: letters, digits, dot, dash or underscore.")
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use a password containing 12-1024 characters.")
    password_hash = generate_password_hash(password, method="scrypt")
    with get_connection() as connection:
        with connection.transaction():
            staff_users.insert_initial_admin(connection, username, password_hash)
