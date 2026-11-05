"""Load local environment settings without connecting to the database."""

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


class Config:
    DATABASE_URL = os.getenv("DATABASE_URL")
    SECRET_KEY = os.getenv("SECRET_KEY")
    STAFF_TEMP_PASSWORD_KEY = os.getenv("STAFF_TEMP_PASSWORD_KEY")
    SESSION_COOKIE_NAME = "tanglaw_staff_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Disable only explicitly for local HTTP development, never for deployment.
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    MAX_CONTENT_LENGTH = 1024 * 1024
