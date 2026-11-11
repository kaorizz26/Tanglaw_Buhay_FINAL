"""Staff sign-in, password help, password setup and protected sign-out endpoints."""

from time import time

from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
import psycopg

from services.auth import (
    ADMIN_ROLE, authenticate, check_csrf, csrf_token, end_session, start_session,
)
from services.initial_password import InitialPasswordError, set_initial_password
from services.staff_password_reset_requests import request_staff_password_reset

auth = Blueprint("auth", __name__)
FORGOT_REQUEST_LIMIT = 5
FORGOT_REQUEST_WINDOW_SECONDS = 15 * 60


def landing_page(user):
    return "main.administration" if user["role"] == ADMIN_ROLE else "main.index"


@auth.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for(
            "auth.change_password" if g.user.get("must_change_password")
            else landing_page(g.user)
        ))
    error = None
    if request.method == "POST":
        check_csrf()
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = None
        try:
            if len(username) <= 64 and len(password) <= 1024:
                user = authenticate(username, password)
        except (psycopg.Error, RuntimeError):
            abort(503, description="Staff authentication is unavailable.")
        if user:
            start_session(user)
            return redirect(url_for(
                "auth.change_password" if user.get("must_change_password")
                else landing_page(user)
            ))
        error = "Invalid username or password."
    return render_template("auth/login.html", error=error, csrf_token=csrf_token()), (
        401 if error else 200
    )


def forgot_request_is_allowed():
    """Apply a small browser-session limit without changing the neutral response."""
    now = int(time())
    attempts = session.get("staff_forgot_password_attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    attempts = [value for value in attempts
                if type(value) is int and now - FORGOT_REQUEST_WINDOW_SECONDS < value <= now]
    allowed = len(attempts) < FORGOT_REQUEST_LIMIT
    if allowed:
        attempts.append(now)
    session["staff_forgot_password_attempts"] = attempts
    return allowed


@auth.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Accept a username without revealing its role, activity or existence."""
    if g.user is not None:
        return redirect(url_for(landing_page(g.user)))
    if request.method == "POST":
        check_csrf()
        if forgot_request_is_allowed():
            try:
                request_staff_password_reset(request.form.get("username", ""))
            except (psycopg.Error, RuntimeError):
                abort(503, description="Password help is unavailable. Please try again.")
        return redirect(url_for("auth.forgot_password", sent=1), code=303)
    return render_template(
        "auth/forgot_password.html", sent=request.args.get("sent") == "1",
        csrf_token=csrf_token(),
    )


@auth.route("/change-password", methods=["GET", "POST"])
def change_password():
    if not g.user.get("must_change_password"):
        return redirect(url_for(landing_page(g.user)))
    error = None
    if request.method == "POST":
        check_csrf()
        try:
            updated_user = set_initial_password(
                g.user, request.form.get("current_password", ""),
                request.form.get("new_password", ""),
                request.form.get("confirm_password", ""),
            )
        except InitialPasswordError as exc:
            error = str(exc)
        except (psycopg.Error, RuntimeError):
            abort(503, description="Password setup is unavailable. Please try again.")
        else:
            start_session(updated_user)
            return redirect(url_for(landing_page(updated_user)))
    return render_template(
        "auth/change_password.html", error=error, csrf_token=csrf_token(),
    ), (400 if error else 200)


@auth.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "POST":
        check_csrf()
        try:
            end_session()
        except (psycopg.Error, RuntimeError):
            abort(503, description="Sign out is unavailable. Please try again.")
        return redirect(url_for("auth.login"))
    return render_template("auth/logout.html", csrf_token=csrf_token())
