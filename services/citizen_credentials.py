"""Create and check citizen activation and password-reset credentials.

Only keyed hashes (HMAC-SHA256) are ever stored. A six-digit code has only a
million possibilities, so a plain hash could be guessed offline if the database
leaked; the secret key and the link token mixed into each code hash prevent that.
"""

from datetime import timedelta
from hmac import compare_digest, new as hmac_new
from hashlib import sha256
import re
from secrets import randbelow, token_urlsafe
from typing import NamedTuple

from flask import current_app

ACTIVATION_LIFETIME = timedelta(hours=48)
PASSWORD_RESET_LIFETIME = timedelta(hours=1)
# The fifth wrong activation code cancels both the link and the code.
MAX_CODE_ATTEMPTS = 5
CODE_DIGITS = 6
MIN_KEY_LENGTH = 32


class ActivationCredentials(NamedTuple):
    """A new link token and code, with the hashes that are safe to store."""

    token: str
    code: str
    token_hash: str
    code_hash: str


class ResetCredentials(NamedTuple):
    """A new password-reset link token and its stored hash."""

    token: str
    token_hash: str


def credential_key():
    """Return the dedicated hashing key; never fall back to the session key."""
    key = current_app.config.get("CITIZEN_CREDENTIAL_KEY")
    if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
        raise RuntimeError("Citizen credential hashing is not configured.")
    return key.encode("utf-8")


def _keyed_hash(*parts):
    """Hash the parts with the secret key; ':' cannot appear in token or code values."""
    message = ":".join(parts).encode("utf-8")
    return hmac_new(credential_key(), message, sha256).hexdigest()


def hash_token(purpose, token):
    """Hash a link token; the purpose keeps an activation link from acting as a reset link."""
    return _keyed_hash("token", purpose, token)


def hash_activation_code(token_hash, code):
    """Hash a code together with its link's hash so it only works for that link."""
    return _keyed_hash("activation-code", token_hash, code)


def new_activation_credentials():
    """Create a random link token and a six-digit code (leading zeros allowed)."""
    token = token_urlsafe(32)
    code = f"{randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    token_hash = hash_token("activation", token)
    return ActivationCredentials(token, code, token_hash, hash_activation_code(token_hash, code))


def new_reset_credentials():
    """Create a random password-reset link token; resets need no code."""
    token = token_urlsafe(32)
    return ResetCredentials(token, hash_token("password_reset", token))


def clean_activation_code(text):
    """Return the six digits from what a citizen typed, or None if it is not a code.

    Spaces and dashes are ignored so "482 913" and "482-913" both work.
    """
    digits = re.sub(r"[\s-]", "", text) if isinstance(text, str) else ""
    return digits if re.fullmatch(rf"[0-9]{{{CODE_DIGITS}}}", digits) else None


def activation_code_matches(stored_code_hash, token_hash, typed_code):
    """Compare a typed code with the stored hash in constant time."""
    code = clean_activation_code(typed_code)
    if code is None:
        return False
    expected = hash_activation_code(token_hash, code)
    return compare_digest(expected.encode("ascii"), stored_code_hash.encode("ascii"))
