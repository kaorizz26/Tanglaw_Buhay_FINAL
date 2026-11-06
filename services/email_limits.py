"""Limits on how many citizen emails one day can start.

Every activation and password-reset email begins with a new row in the link table,
so the limits count those rows. A row is counted when the link is created, whether or
not the mail server later accepts the message or the citizen receives it: failed
attempts count too, and cancelled or used links are never forgotten within the day.
That is intentional, because it keeps the limits impossible to reset by cancelling or
by changing the account's email address.

The caller must already hold the account's row lock (the Staff flows and the password
reset do), and must call these functions inside the transaction that inserts the link.
Taking a database lock for the daily count means two simultaneous requests can never
both use the last remaining slot.
"""

from datetime import timedelta

from repositories import citizen_accounts

WINDOW = timedelta(hours=24)
# Gmail allows roughly 500 messages a day; stay well below it.
DAILY_EMAIL_LIMIT = 200
MAX_ACTIVATION_EMAILS_PER_ACCOUNT = 5

ACCOUNT_LIMIT = "account_email_limit"
DAILY_LIMIT = "daily_email_limit"


def daily_budget_left(connection):
    """True if another email may start today. Locks the daily count until the transaction ends."""
    citizen_accounts.lock_email_budget(connection)
    return citizen_accounts.count_all_recent_tokens(connection, WINDOW) < DAILY_EMAIL_LIMIT


def activation_email_problem(connection, account_id):
    """Return a fixed limit code if a new activation email must not start, else None."""
    sent = citizen_accounts.count_recent_tokens(connection, account_id, "activation", WINDOW)
    if sent >= MAX_ACTIVATION_EMAILS_PER_ACCOUNT:
        return ACCOUNT_LIMIT
    if not daily_budget_left(connection):
        return DAILY_LIMIT
    return None
