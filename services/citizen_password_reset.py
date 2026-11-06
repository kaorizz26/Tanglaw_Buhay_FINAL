"""Citizen "forgot password": request a reset link, then choose a new password.

The answer to a request never depends on whether the email address has an account,
so nobody can use this page to find out who is registered. The email is sent only
after the database change has been committed.
"""

from datetime import timedelta

import psycopg
from werkzeug.security import generate_password_hash

from db import get_connection
from repositories import citizen_accounts
from services.citizen_authentication import (
    MAX_EMAIL_LENGTH, PortalUnavailable, check_new_password, looks_like_token,
)
from services.citizen_credentials import (
    PASSWORD_RESET_LIFETIME, hash_token, new_reset_credentials,
)
from services import email_limits
from services.email_delivery import (
    EmailDeliveryError, send_password_changed_email, send_password_reset_email,
)

PURPOSE = "password_reset"
MAX_RESET_REQUESTS = 3
REQUEST_WINDOW = timedelta(hours=1)


class ResetLinkError(Exception):
    """The reset link cannot be used (unknown, used, replaced, expired or inactive account)."""


# ---- Step 1: ask for a reset link -------------------------------------------------

def request_password_reset(email, *, connection_factory=get_connection):
    """Email a reset link if the address belongs to an active account.

    Returns nothing in every case. Raises PortalUnavailable only when the database
    or the secret key is not available, which says nothing about the address.
    """
    email = email.strip().lower() if isinstance(email, str) else ""
    try:
        # Created first for every request, so a missing secret key fails the same way
        # whether or not the address is registered.
        credentials = new_reset_credentials()
        if not email or len(email) > MAX_EMAIL_LENGTH:
            return
        link_saved = _save_reset_link(connection_factory, email, credentials.token_hash)
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    if link_saved:
        try:
            send_password_reset_email(email, credentials.token)
        except EmailDeliveryError:
            pass  # The citizen sees the same page; the request still counts toward the limit.


def _save_reset_link(connection_factory, email, token_hash):
    """Store a new one-hour link unless the account is unknown, inactive or over the limit.

    The account row is locked, so simultaneous requests are counted one at a time
    and can never create more than the allowed number of links.
    """
    with connection_factory() as connection:
        with connection.transaction():
            account = citizen_accounts.find_by_email(connection, email, lock=True)
            if account is None or account["status"] != "active":
                return False
            recent = citizen_accounts.count_recent_tokens(
                connection, account["id"], PURPOSE, REQUEST_WINDOW,
            )
            if recent >= MAX_RESET_REQUESTS:
                return False
            if not email_limits.daily_budget_left(connection):
                return False  # Same silent answer as any other no-send, so nothing is revealed.
            citizen_accounts.revoke_open_tokens(connection, account["id"], PURPOSE)
            citizen_accounts.insert_token(
                connection, account["id"], PURPOSE, token_hash, None, PASSWORD_RESET_LIFETIME,
            )
            return True


# ---- Step 2: open the link and choose a new password ---------------------------------

def reset_link_is_usable(token, *, connection_factory=get_connection):
    """True if the link can still be used. Looking at it never uses it up."""
    if not looks_like_token(token):
        return False
    try:
        with connection_factory() as connection:
            return _open_link(connection, hash_token(PURPOSE, token)) is not None
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None


def _open_link(connection, token_hash, *, lock=False):
    """Return the link's record if it is usable and its account is active, else None."""
    record = citizen_accounts.find_open_token(connection, token_hash, PURPOSE, lock=lock)
    if record is None or record["account_status"] != "active":
        return None
    return record


def reset_password(token, password, confirmation, *, connection_factory=get_connection):
    """Save the new password, use up the link and sign the citizen out everywhere.

    Raises ResetLinkError for an unusable link and CitizenFormError for password
    mistakes the citizen can fix (these do not use up the link).
    """
    if not looks_like_token(token):
        raise ResetLinkError
    try:
        token_hash = hash_token(PURPOSE, token)
        with connection_factory() as connection:
            record = _open_link(connection, token_hash)
            if record is None:
                raise ResetLinkError
            email = citizen_accounts.find_by_id(connection, record["account_id"])["email"]
        check_new_password(password, confirmation, email)
        account = _use_link(connection_factory, token_hash, generate_password_hash(
            password, method="scrypt"))
    except (psycopg.Error, RuntimeError):
        raise PortalUnavailable from None
    # The password is already changed; a failed notice must not undo or hide that.
    try:
        send_password_changed_email(account["email"])
    except EmailDeliveryError:
        pass


def _use_link(connection_factory, token_hash, password_hash):
    """Use the link and change the password in one transaction.

    The link row is locked, so two requests cannot both use it. replace_password
    also raises the session version, which ends every existing citizen session.
    """
    with connection_factory() as connection:
        with connection.transaction():
            record = _open_link(connection, token_hash, lock=True)
            if record is None or not citizen_accounts.consume_token(connection, record["id"]):
                raise ResetLinkError
            account = citizen_accounts.replace_password(
                connection, record["account_id"], password_hash,
            )
            if account is None:
                raise ResetLinkError  # The account stopped being active; nothing is saved.
            return account
