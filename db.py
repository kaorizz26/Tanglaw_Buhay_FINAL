"""Open PostgreSQL connections explicitly when a caller needs one."""

import psycopg
from flask import current_app


def get_connection():
    """Return a connection; the caller manages its transaction and lifetime."""
    database_url = current_app.config.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in .env before using the database.")
    return psycopg.connect(database_url)
