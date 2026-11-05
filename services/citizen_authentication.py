"""Citizen activation, sign-in and password change.

Failed attempts (wrong code, wrong password) must be saved even though the request
is refused. So each function finishes its database transaction first and only then
raises the error, otherwise the rollback would erase the count and the limit would
never be reached.
"""

from secrets import token_urlsafe

import psycopg
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection
from repositories import citizen_accounts
from services.citizen_credentials import (
    MAX_CODE_ATTEMPTS, activation_code_matches, clean_activation_code, hash_token,
)

MAX_LOGIN_FAILURES = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_EMAIL_LENGTH = 254
TOKEN_PATTERN_LENGTH = (20, 100)
# Unknown emails still pay for one password check, so timing does not reveal them.
_DUMMY_HASH = generate_password_hash(token_urlsafe(32), method="scrypt")


class CitizenFormError(ValueError):
    """The form needs correction; the message is plain English for the citizen."""


class ActivationLinkError(Exception):
    """The activation link cannot be used any more.

    `reason` is "invalid" (unknown, used, expired or replaced link) or
    "cancelled" (too many wrong codes).
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class SignInError(Exception):
    """Sign-in refused. One message is used for every reason so nothing is revealed."""


class PortalUnavailable(Exception):
    """The database or a required secret is not available."""


def looks_like_token(token):
    low, high = TOKEN_PATTERN_LENGTH
    return (isinstance(token, str) and low <= len(token) <= high
            and all(c.isalnum() or c in "-_" for c in token) and token.isascii())


def check_new_password(password, confirmation, email, current_password=None):
    """Apply the password rules; raise CitizenFormError with a helpful message."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise CitizenFormError(
            f"Your password must have at least {MIN_PASSWORD_LENGTH} characters."
        )
    if password != confirmation:
        raise CitizenFormError("The two passwords do not match. Type the same password twice.")
    if password.strip().lower() == email.lower():
        raise CitizenFormError("Your password cannot be your email address.")
    if current_password is not None and password == current_password:
        raise CitizenFormError("Choose a new password that is different from your current one.")


# ---- Activation -----------------------------------------------------------------

def activation_link_is_usable(token, *, connection_factory=get_connection):
    """True if the emailed link can still be used (checked without counting an attempt)."""
    if not looks_like_token(token):
        return False
    try:
        with connection_factory() as connection:
            record = citizen_accounts.find_open_token(
                connection, hash_token("activation", token), "activation",
            )
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    return record is not None and record["account_status"] == "pending_activation"


def activate_account(token, code, password, confirmation, *,
                     connection_factory=get_connection):
    """Check the code and activate the account with the citizen's own password.

    Raises ActivationLinkError when the link is unusable and CitizenFormError for
    mistakes the citizen can correct.
    """
    if not looks_like_token(token):
        raise ActivationLinkError("invalid")
    if clean_activation_code(code) is None:
        raise CitizenFormError("Enter the 6-digit code that LGU Staff gave you.")
    try:
        token_hash = hash_token("activation", token)
        with connection_factory() as connection:
            email = _pending_email(connection, token_hash)
        check_new_password(password, confirmation, email)
        password_hash = generate_password_hash(password, method="scrypt")
        outcome = _try_activation(connection_factory, token_hash, code, password_hash)
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    if outcome == "activated":
        return
    if outcome == "cancelled":
        raise ActivationLinkError("cancelled")
    if outcome == "invalid":
        raise ActivationLinkError("invalid")
    tries_left = outcome[1]
    raise CitizenFormError(
        f"That code is not correct. You have {tries_left} "
        f"{'try' if tries_left == 1 else 'tries'} left. Check the code and try again."
    )


def _pending_email(connection, token_hash):
    """Return the email of the account this link belongs to, or stop if the link is unusable."""
    record = citizen_accounts.find_open_token(connection, token_hash, "activation")
    if record is None or record["account_status"] != "pending_activation":
        raise ActivationLinkError("invalid")
    account = citizen_accounts.find_by_id(connection, record["account_id"])
    return account["email"]


def _try_activation(connection_factory, token_hash, code, password_hash):
    """Do the check and its database changes in one transaction.

    The link row stays locked until the commit, so two requests cannot both
    count a wrong code or both use the link. Returns "activated", "invalid",
    "cancelled" or ("wrong_code", tries_left); the caller raises afterwards.
    """
    with connection_factory() as connection:
        with connection.transaction():
            record = citizen_accounts.find_open_token(
                connection, token_hash, "activation", lock=True,
            )
            if record is None or record["account_status"] != "pending_activation":
                return "invalid"
            if not activation_code_matches(record["code_hash"], token_hash, code):
                failure = citizen_accounts.record_failed_code_attempt(
                    connection, record["id"], MAX_CODE_ATTEMPTS,
                )
                if failure is None or failure["revoked"]:
                    return "cancelled"
                return ("wrong_code", MAX_CODE_ATTEMPTS - failure["failed_code_attempts"])
            if not citizen_accounts.consume_token(connection, record["id"]):
                return "invalid"
            if citizen_accounts.activate(connection, record["account_id"], password_hash) is None:
                raise RuntimeError("activation of a pending account failed")
            return "activated"


# ---- Sign-in --------------------------------------------------------------------

def sign_in(email, password, *, connection_factory=get_connection):
    """Return the signed-in account (without its password hash) or raise SignInError."""
    email = email.strip().lower() if isinstance(email, str) else ""
    if not email or len(email) > MAX_EMAIL_LENGTH or not 1 <= len(password) <= MAX_PASSWORD_LENGTH:
        check_password_hash(_DUMMY_HASH, password[:MAX_PASSWORD_LENGTH])
        raise SignInError
    try:
        account = _check_password_and_count(connection_factory, email, password)
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    if account is None:
        raise SignInError
    return account


def _check_password_and_count(connection_factory, email, password):
    """Verify the password and record the result in one committed transaction."""
    with connection_factory() as connection:
        with connection.transaction():
            citizen_accounts.clear_expired_lock(connection, email)
            # The lock makes simultaneous attempts wait their turn, so the count is exact.
            login = citizen_accounts.find_by_email(connection, email, lock=True)
            usable = login is not None and login["status"] == "active" and login["password_hash"]
            password_ok = check_password_hash(login["password_hash"] if usable else _DUMMY_HASH, password)
            if not usable:
                return None
            if login["locked_until"] is not None:
                return None  # Locked: even the right password is refused, and nothing is counted.
            if not password_ok:
                citizen_accounts.record_failed_login(
                    connection, login["id"], MAX_LOGIN_FAILURES, LOCK_MINUTES,
                )
                return None
            citizen_accounts.record_successful_login(connection, login["id"])
            return {key: value for key, value in login.items() if key != "password_hash"}


# ---- Change password ------------------------------------------------------------

def change_password(account, current_password, new_password, confirmation, *,
                    connection_factory=get_connection):
    """Replace the password of the signed-in citizen and return the updated account.

    A wrong current password counts toward the same five-attempt lock as sign-in.
    When that limit is reached every session of the account is ended.
    """
    if not 1 <= len(current_password) <= MAX_PASSWORD_LENGTH:
        raise CitizenFormError("Enter your current password.")
    check_new_password(new_password, confirmation, account["email"], current_password)
    try:
        outcome, updated = _replace_password(
            connection_factory, account, current_password,
            generate_password_hash(new_password, method="scrypt"),
        )
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    if outcome == "changed":
        return updated
    if outcome == "locked":
        raise SignInError
    raise CitizenFormError("Your current password is not correct.")


def _replace_password(connection_factory, account, current_password, new_hash):
    """Returns ("changed", account), ("wrong_password", None) or ("locked", None)."""
    with connection_factory() as connection:
        with connection.transaction():
            login = citizen_accounts.find_by_email(connection, account["email"], lock=True)
            if (login is None or login["id"] != account["id"] or login["status"] != "active"
                    or login["session_version"] != account["session_version"]
                    or login["locked_until"] is not None):
                return "locked", None
            if not check_password_hash(login["password_hash"], current_password):
                result = citizen_accounts.record_failed_login(
                    connection, login["id"], MAX_LOGIN_FAILURES, LOCK_MINUTES,
                )
                if result["locked_until"] is not None:
                    citizen_accounts.end_sessions(connection, login["id"])
                    return "locked", None
                return "wrong_password", None
            updated = citizen_accounts.replace_password(connection, login["id"], new_hash)
            return "changed", updated
