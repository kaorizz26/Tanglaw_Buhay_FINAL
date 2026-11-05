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
    # Dedicated secret for keyed hashes of citizen activation and reset credentials.
    CITIZEN_CREDENTIAL_KEY = os.getenv("CITIZEN_CREDENTIAL_KEY")
    # Citizen emails. Values stay plain text here and are validated when mail is sent,
    # so a missing or wrong setting never stops the rest of the application starting.
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL")
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = os.getenv("SMTP_PORT")
    SMTP_SECURITY = os.getenv("SMTP_SECURITY")
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM = os.getenv("SMTP_FROM")
    # Optional comma-separated addresses; when set, no other recipient receives mail.
    SMTP_ALLOWED_RECIPIENTS = os.getenv("SMTP_ALLOWED_RECIPIENTS")
    SESSION_COOKIE_NAME = "tanglaw_staff_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Disable only explicitly for local HTTP development, never for deployment.
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    MAX_CONTENT_LENGTH = 1024 * 1024
