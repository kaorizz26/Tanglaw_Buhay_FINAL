"""Citizen Portal sessions, kept completely separate from LGU Staff sessions.

A citizen's session is one signed cookie with its own name, signing salt and path.
It holds only the account id, the account's session version, and a CSRF token.
The lifetime is fixed at sign-in: the cookie is never re-issued while the citizen
browses, so a session cannot be extended silently. Every request rechecks the
account in the database, so deactivating an account, changing a password or
signing out ends the session at once.
"""

from functools import wraps
from hmac import compare_digest
from secrets import token_urlsafe

from flask import abort, current_app, g, redirect, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
import psycopg

from db import get_connection
from repositories import citizen_accounts

SIGNING_SALT = "tanglaw-citizen-session"
COOKIE_PATH = "/portal"
MIN_SECRET_LENGTH = 32


def sessions_configured():
    """True only when the signing secret is strong enough; otherwise the portal stays closed."""
    key = current_app.secret_key
    return isinstance(key, (str, bytes)) and len(key) >= MIN_SECRET_LENGTH


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt=SIGNING_SALT)


def _lifetime_seconds():
    return current_app.config["CITIZEN_SESSION_LIFETIME"].total_seconds()


def _anonymous_payload():
    """A session with no signed-in citizen; it still carries a CSRF token for forms."""
    return {"csrf": token_urlsafe(32)}


def _read_cookie():
    """Return the verified cookie contents, or None if missing, forged or expired."""
    value = request.cookies.get(current_app.config["CITIZEN_SESSION_COOKIE_NAME"])
    if not value:
        return None
    try:
        payload = _serializer().loads(value, max_age=_lifetime_seconds())
    except BadSignature:  # Also covers expired signatures.
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("csrf"), str):
        return None
    return payload


def _account_for(payload):
    """Return the signed-in account if the payload still matches the database, else None."""
    account_id = payload.get("account_id")
    version = payload.get("session_version")
    if type(account_id) is not int or type(version) is not int:
        return None
    with get_connection() as connection:
        account = citizen_accounts.find_by_id(connection, account_id)
    if (account is None or account["status"] != "active"
            or account["session_version"] != version):
        return None
    return account


def load_session():
    """Run before every portal request: find the signed-in citizen, if any."""
    g.citizen = None
    g.citizen_cookie_changed = False
    payload = _read_cookie()
    if payload is not None and "account_id" in payload:
        try:
            g.citizen = _account_for(payload)
        except psycopg.Error:
            abort(503, description="The Citizen Portal is unavailable. Please try again later.")
        if g.citizen is None:
            payload = None  # Revoked or expired account session: start over.
    if payload is None:
        payload = _anonymous_payload()
        g.citizen_cookie_changed = True
    g.citizen_payload = payload


def begin_session(account):
    """Sign a citizen in with a brand-new cookie, so an old cookie cannot be reused."""
    g.citizen = account
    g.citizen_payload = {
        "csrf": token_urlsafe(32), "account_id": account["id"],
        "session_version": account["session_version"],
    }
    g.citizen_cookie_changed = True


def end_session(account_id):
    """Sign out on every device, then leave the browser with a signed-out cookie."""
    with get_connection() as connection:
        citizen_accounts.end_sessions(connection, account_id)
    g.citizen = None
    g.citizen_payload = _anonymous_payload()
    g.citizen_cookie_changed = True


def save_cookie(response):
    """Run after every portal request: write the cookie only when it actually changed."""
    if g.get("citizen_cookie_changed"):
        config = current_app.config
        response.set_cookie(
            config["CITIZEN_SESSION_COOKIE_NAME"],
            _serializer().dumps(g.citizen_payload),
            max_age=int(_lifetime_seconds()), path=COOKIE_PATH, httponly=True,
            samesite="Lax", secure=config["SESSION_COOKIE_SECURE"],
        )
    return response


def csrf_token():
    return g.citizen_payload["csrf"]


def check_csrf():
    """Reject a form whose hidden token does not match this browser's session."""
    supplied = request.form.get("csrf_token", "")
    if not compare_digest(csrf_token().encode("utf-8"), supplied.encode("utf-8")):
        abort(400, description="This page expired. Go back, reload it and try again.")


def citizen_required(view):
    """Send visitors who are not signed in to the Citizen Portal sign-in page."""

    @wraps(view)
    def guarded(*args, **kwargs):
        if g.citizen is None:
            return redirect(url_for("citizen_portal.login"))
        return view(*args, **kwargs)
    return guarded
