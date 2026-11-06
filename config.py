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
    # Who may receive mail: 'allowlist' (default; needs the list below) or exactly 'any'.
    SMTP_RECIPIENT_MODE = os.getenv("SMTP_RECIPIENT_MODE")
    # Comma-separated addresses for 'allowlist' mode; must be empty in 'any' mode.
    SMTP_ALLOWED_RECIPIENTS = os.getenv("SMTP_ALLOWED_RECIPIENTS")
    # Digital Dropbox: private folder for uploaded documents. It must be outside the
    # project and static/, and is backed up together with the database.
    DOCUMENT_STORAGE_DIR = os.getenv("DOCUMENT_STORAGE_DIR")
    SESSION_COOKIE_NAME = "tanglaw_staff_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Disable only explicitly for local HTTP development, never for deployment.
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = False
    # Citizen Portal sessions use their own cookie; the lifetime is fixed from sign-in.
    CITIZEN_SESSION_COOKIE_NAME = "tanglaw_citizen_session"
    CITIZEN_SESSION_LIFETIME = timedelta(hours=2)
    MAX_CONTENT_LENGTH = 1024 * 1024
    IMPORT_MAX_CONTENT_LENGTH = 256 * 1024 * 1024
    IMPORT_STAGING_BYTES = 1024 * 1024 * 1024
    IMPORT_STAGING_DIR = os.getenv("IMPORT_STAGING_DIR")
    # The documented 2022 pilot batch provides the approved reference snapshot.
    IMPORT_REFERENCE_BATCH_ID = 1
