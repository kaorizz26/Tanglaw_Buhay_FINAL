"""Staff-side Citizen Account creation and activation resend.

The activation link is built and emailed here and is never returned to a route
or template. Staff only ever receive the six-digit code, once.
"""

import re

import psycopg

from db import get_connection
from repositories import assessment_evidence, assessment_runs, dataset_batches
from repositories import citizen_accounts, staff_reviews
from services.assessment_details import format_contact_number
from services.assessment_queries import AssessmentQueryError, _read_context
from services.citizen_account_panel import present_account_panel, present_history
from services.citizen_credentials import (
    ACTIVATION_LIFETIME, new_activation_credentials,
)
from services import email_limits
from services.email_delivery import (
    ERROR_MESSAGES, EmailDeliveryError, send_activation_email, send_email_changed_notice,
)
from services.prioritization import _head_name, _household_reference_id, _source_text

EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 254
MIN_NOTE_LENGTH = 10
MAX_NOTE_LENGTH = 500
# PostgreSQL names these automatically from the table and columns in migration 0014.
HOUSEHOLD_UNIQUE = "citizen_accounts_batch_id_household_uuid_key"
EMAIL_UNIQUE = "citizen_accounts_email_key"
RESEND_NOTE = "Activation email resent."
EMAIL_CONSENT_MESSAGE = "Confirm that the representative agreed and checked the email address."


class CitizenAccountInputError(ValueError):
    """The form needs correction; `errors` maps field names to plain-English messages."""

    def __init__(self, errors):
        super().__init__("invalid_form")
        self.errors = errors


class CitizenAccountError(RuntimeError):
    """A request cannot be completed; the message is a fixed code, never private data."""


def _check_name(form, errors):
    name = " ".join(form.get("representative_name", "").split())
    if not 1 <= len(name) <= MAX_NAME_LENGTH or not name.isprintable():
        errors["representative_name"] = (
            f"Enter the representative's full name (up to {MAX_NAME_LENGTH} characters)."
        )
    return name


def _check_email(form, errors):
    """Check an email and its confirmation; return the cleaned address."""
    email = form.get("email", "").strip().lower()
    confirmation = form.get("email_confirmation", "").strip().lower()
    if (len(email) > MAX_EMAIL_LENGTH or not email.isascii()
            or not EMAIL_PATTERN.fullmatch(email)):
        errors["email"] = "Enter a valid email address, for example name@example.com."
    elif confirmation != email:
        errors["email_confirmation"] = "The two email addresses do not match."
    return email


def _check_confirmations(form, errors, consent_message):
    """Both staff confirmations must be ticked."""
    if form.get("representative_verified") != "on":
        errors["representative_verified"] = "Confirm that you spoke with the representative."
    if form.get("privacy_consent_confirmed") != "on":
        errors["privacy_consent_confirmed"] = consent_message


def _check_note(form, field, errors):
    note = form.get(field, "").strip()
    if not MIN_NOTE_LENGTH <= len(note) <= MAX_NOTE_LENGTH or "\x00" in note:
        errors[field] = (
            f"Write a short note of {MIN_NOTE_LENGTH} to {MAX_NOTE_LENGTH} characters."
        )
    return note


def validate_creation_form(form):
    """Return the cleaned form values, or raise CitizenAccountInputError."""
    errors = {}
    name = _check_name(form, errors)
    email = _check_email(form, errors)
    _check_confirmations(form, errors, EMAIL_CONSENT_MESSAGE)
    note = _check_note(form, "verification_note", errors)
    if errors:
        raise CitizenAccountInputError(errors)
    return {"representative_name": name, "email": email, "verification_note": note}


def validate_deactivation_form(form):
    """Return the cleaned reason for deactivating, or raise CitizenAccountInputError."""
    errors = {}
    note = _check_note(form, "reason_note", errors)
    if errors:
        raise CitizenAccountInputError(errors)
    return {"note": note}


def validate_reactivation_form(form):
    """Return the cleaned note once both confirmations are ticked, or raise."""
    errors = {}
    _check_confirmations(
        form, errors, "Confirm that the representative agreed to reactivate the account.",
    )
    note = _check_note(form, "verification_note", errors)
    if errors:
        raise CitizenAccountInputError(errors)
    return {"note": note}


def validate_email_change_form(form):
    """Return the new address and note once both confirmations are ticked, or raise."""
    errors = {}
    email = _check_email(form, errors)
    _check_confirmations(form, errors, EMAIL_CONSENT_MESSAGE)
    note = _check_note(form, "verification_note", errors)
    if errors:
        raise CitizenAccountInputError(errors)
    return {"email": email, "note": note}


def format_activation_code(code):
    """Group the code for reading aloud: 482913 becomes '482 913'."""
    return f"{code[:3]} {code[3:]}"


def read_household_header(connection, context, household_uuid):
    """Read what staff pages show about a household: who and where it is.

    Raises AssessmentQueryError if the household has no stored assessment in the
    selected survey run. The batch and run IDs are included so forms can post them
    back, and a change of the selected batch between viewing and saving is noticed.
    """
    if not assessment_runs.household_has_assessment(
            connection, context["run_id"], household_uuid):
        raise AssessmentQueryError("household_assessment_missing")
    identities = dataset_batches.find_household_identities(
        connection, context["batch_id"], [household_uuid],
    )
    if len(identities) != 1:
        raise AssessmentQueryError("household_identity_missing")
    contact = assessment_evidence.find_household_contact_number(
        connection, context["batch_id"], household_uuid,
    )
    identity = identities[0]
    return {
        "batch_id": context["batch_id"], "run_id": context["run_id"],
        "household_uuid": household_uuid, "contact_number": format_contact_number(contact),
        "reference_id": _household_reference_id(identity["source_row_number"]),
        "head_name": _head_name(identity),
        "barangay": _source_text(identity["barangay"]) or "Not recorded",
    }


def get_household_account_page(household_uuid, *, with_history=False,
                               connection_factory=get_connection):
    """Read the household details and account state for the create and resend pages.

    with_history adds the account's staff actions, newest first, for the Account
    History page.
    """
    with _read_context(connection_factory) as (connection, context):
        page = read_household_header(connection, context, household_uuid)
        row = citizen_accounts.find_panel(connection, context["batch_id"], household_uuid)
        events = (citizen_accounts.list_events(connection, row["id"])
                  if with_history and row else [])
    page["account"] = present_account_panel(row)
    if with_history:
        page["history"] = present_history(events)
    return page


def _issue_activation_credentials(connection, account_id):
    """Replace any open activation pair with a new one; return (token id, credentials).

    Only hashes are stored. The caller emails the link after the commit. Creating an
    account, resending, reactivating and changing the email all come through here, so
    one check applies the per-account and daily email limits to every one of them.
    """
    limit_code = email_limits.activation_email_problem(connection, account_id)
    if limit_code:
        raise CitizenAccountError(limit_code)
    credentials = new_activation_credentials()
    citizen_accounts.revoke_open_tokens(connection, account_id, "activation")
    token = citizen_accounts.insert_token(
        connection, account_id, "activation", credentials.token_hash,
        credentials.code_hash, ACTIVATION_LIFETIME,
    )
    return token["id"], credentials


def check_actor_and_context(connection, staff_id, batch_id, run_id, household_uuid):
    """Confirm the survey selection is unchanged, the actor is active LGU Staff and the
    household has a stored assessment in that selection."""
    if not staff_reviews.lock_selected_context(connection, batch_id, run_id):
        raise CitizenAccountError("context_changed")
    if not citizen_accounts.active_lgu_staff_exists(connection, staff_id):
        raise CitizenAccountError("unauthorized")
    if not assessment_runs.household_has_assessment(connection, run_id, household_uuid):
        raise CitizenAccountError("household_missing")


def check_request_values(staff_id, household_uuid, batch_id, run_id):
    """Reject malformed identifiers before any database work."""
    if (not isinstance(household_uuid, str) or not household_uuid.strip()
            or len(household_uuid) > 128 or "\x00" in household_uuid
            or any(type(value) is not int or value < 1
                   for value in (staff_id, batch_id, run_id))):
        raise CitizenAccountError("invalid_request")


def lock_household_account(connection, staff_id, batch_id, run_id, household_uuid):
    """Run the actor and context checks, then return the household's account, locked."""
    check_actor_and_context(connection, staff_id, batch_id, run_id, household_uuid)
    existing = citizen_accounts.find_by_household(connection, batch_id, household_uuid)
    if existing is None:
        raise CitizenAccountError("account_missing")
    return citizen_accounts.find_by_id(connection, existing["id"], lock=True)


def _send_and_record(token_id, email, token, connection_factory):
    """Email the link after the commit, then record that the server accepted it.

    Returns None on success or the staff-safe reason the email was not sent.
    """
    try:
        send_activation_email(email, token)
    except EmailDeliveryError as failure:
        return ERROR_MESSAGES[failure.code]
    try:
        with connection_factory() as connection:
            citizen_accounts.mark_email_accepted(connection, token_id)
    except psycopg.Error:
        # The email was sent; only the status note is missing, and Resend fixes it.
        pass
    return None


def _delivery_result(account, credentials, token_id, email_problem):
    """Describe the outcome for staff. The code is shown only when the email went out,
    because a failed email is replaced by a Resend with a new code anyway."""
    sent = email_problem is None
    return {
        "email": account["email"], "email_sent": sent, "email_problem": email_problem,
        "activation_code": format_activation_code(credentials.code) if sent else None,
        "valid_hours": int(ACTIVATION_LIFETIME.total_seconds() // 3600),
    }


def create_citizen_account(staff_id, household_uuid, batch_id, run_id, form, *,
                           connection_factory=get_connection):
    """Create a Pending Activation account, record the action and email the link.

    Raises CitizenAccountInputError for form mistakes and CitizenAccountError with
    a fixed code otherwise. Nothing is created if any database step fails.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    values = validate_creation_form(form)
    try:
        with connection_factory() as connection:
            try:
                with connection.transaction():
                    check_actor_and_context(
                        connection, staff_id, batch_id, run_id, household_uuid,
                    )
                    account = citizen_accounts.insert_account(
                        connection, batch_id, household_uuid,
                        values["representative_name"], values["email"],
                    )
                    citizen_accounts.insert_event(
                        connection, account["id"], staff_id, "created",
                        values["verification_note"], representative_verified=True,
                        privacy_consent_confirmed=True,
                    )
                    token_id, credentials = _issue_activation_credentials(
                        connection, account["id"],
                    )
            except psycopg.errors.UniqueViolation as duplicate:
                constraint = duplicate.diag.constraint_name
                if constraint == HOUSEHOLD_UNIQUE:
                    raise CitizenAccountError("household_has_account") from None
                if constraint == EMAIL_UNIQUE:
                    raise CitizenAccountError("email_in_use") from None
                raise
    except (CitizenAccountError, CitizenAccountInputError):
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    problem = _send_and_record(token_id, account["email"], credentials.token, connection_factory)
    return _delivery_result(account, credentials, token_id, problem)


def resend_activation(staff_id, household_uuid, batch_id, run_id, *,
                      connection_factory=get_connection):
    """Replace the activation link and code of a Pending account and email the new link.

    The old link and code stop working at once, even if the new email fails.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    try:
        with connection_factory() as connection:
            with connection.transaction():
                account = lock_household_account(
                    connection, staff_id, batch_id, run_id, household_uuid,
                )
                if account["status"] != "pending_activation":
                    raise CitizenAccountError("not_pending")
                token_id, credentials = _issue_activation_credentials(connection, account["id"])
                citizen_accounts.insert_event(
                    connection, account["id"], staff_id, "activation_resent", RESEND_NOTE,
                )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    problem = _send_and_record(token_id, account["email"], credentials.token, connection_factory)
    return _delivery_result(account, credentials, token_id, problem)


def deactivate_account(staff_id, household_uuid, batch_id, run_id, form, *,
                       connection_factory=get_connection):
    """Block a Pending or Active account, end its sessions and cancel every open link.

    Nothing is emailed. The account stays linked to its household and email.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    values = validate_deactivation_form(form)
    try:
        with connection_factory() as connection:
            with connection.transaction():
                account = lock_household_account(
                    connection, staff_id, batch_id, run_id, household_uuid,
                )
                if account["status"] == "deactivated":
                    raise CitizenAccountError("not_deactivatable")
                citizen_accounts.deactivate(connection, account["id"])
                citizen_accounts.revoke_open_tokens(connection, account["id"])
                citizen_accounts.insert_event(
                    connection, account["id"], staff_id, "deactivated", values["note"],
                )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None


def reactivate_account(staff_id, household_uuid, batch_id, run_id, form, *,
                       connection_factory=get_connection):
    """Return a Deactivated account to Pending Activation and email a new link.

    The old password is removed, so the citizen must activate again with a new code.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    values = validate_reactivation_form(form)
    try:
        with connection_factory() as connection:
            with connection.transaction():
                account = lock_household_account(
                    connection, staff_id, batch_id, run_id, household_uuid,
                )
                if account["status"] != "deactivated":
                    raise CitizenAccountError("not_deactivated")
                account = citizen_accounts.return_to_pending(connection, account["id"])
                citizen_accounts.revoke_open_tokens(connection, account["id"])
                token_id, credentials = _issue_activation_credentials(connection, account["id"])
                citizen_accounts.insert_event(
                    connection, account["id"], staff_id, "reactivated", values["note"],
                    representative_verified=True, privacy_consent_confirmed=True,
                )
    except CitizenAccountError:
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    problem = _send_and_record(token_id, account["email"], credentials.token, connection_factory)
    return _delivery_result(account, credentials, token_id, problem)


def _notify_previous_address(address):
    """Send the security notice to the old address; a failure never undoes the change."""
    try:
        send_email_changed_notice(address)
    except EmailDeliveryError:
        return False
    return True


def change_account_email(staff_id, household_uuid, batch_id, run_id, form, *,
                         connection_factory=get_connection):
    """Move a Pending or Active account to a new email and require activation again.

    Sessions, the old password and every open link end at once. The new address
    gets the activation link; the old address gets a notice that does not name it.
    """
    check_request_values(staff_id, household_uuid, batch_id, run_id)
    values = validate_email_change_form(form)
    try:
        with connection_factory() as connection:
            try:
                with connection.transaction():
                    account = lock_household_account(
                        connection, staff_id, batch_id, run_id, household_uuid,
                    )
                    if account["status"] == "deactivated":
                        raise CitizenAccountError("deactivated")
                    previous_email = account["email"]
                    if values["email"] == previous_email:
                        raise CitizenAccountInputError(
                            {"email": "This is already the registered email address."},
                        )
                    account = citizen_accounts.return_to_pending(
                        connection, account["id"], values["email"],
                    )
                    citizen_accounts.revoke_open_tokens(connection, account["id"])
                    token_id, credentials = _issue_activation_credentials(
                        connection, account["id"],
                    )
                    citizen_accounts.insert_event(
                        connection, account["id"], staff_id, "email_changed", values["note"],
                        representative_verified=True, privacy_consent_confirmed=True,
                    )
            except psycopg.errors.UniqueViolation as duplicate:
                if duplicate.diag.constraint_name == EMAIL_UNIQUE:
                    raise CitizenAccountError("email_in_use") from None
                raise
    except (CitizenAccountError, CitizenAccountInputError):
        raise
    except (psycopg.Error, RuntimeError):
        raise CitizenAccountError("unavailable") from None
    problem = _send_and_record(token_id, account["email"], credentials.token, connection_factory)
    result = _delivery_result(account, credentials, token_id, problem)
    result["previous_email_notified"] = _notify_previous_address(previous_email)
    return result
