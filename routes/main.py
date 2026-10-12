"""Minimal application health route."""

from flask import Blueprint, jsonify

main = Blueprint("main", __name__)


@main.get("/test")
def test_route():
    return jsonify(status="ok", application="Tanglaw-Buhay")
