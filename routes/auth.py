"""Minimal login and CSRF-protected logout endpoints."""

from flask import Blueprint, abort, g, redirect, render_template, request, url_for
import psycopg

from services.auth import authenticate, check_csrf, csrf_token, end_session, start_session

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(url_for("assessments.index"))
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
            return redirect(url_for("assessments.index"))
        error = "Invalid username or password."
    return render_template("auth/login.html", error=error, csrf_token=csrf_token()), (
        401 if error else 200
    )


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
