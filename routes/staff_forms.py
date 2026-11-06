"""Helpers shared by the staff pages that post forms (Citizen Accounts and Document Requests)."""

from secrets import token_urlsafe

from flask import abort, g, request, session

from services.auth import csrf_token

# Each form carries a one-time number. A repeated POST (a browser refresh or a double
# click) finds it already used and changes nothing, so a resend cannot silently
# replace the code the staff member has just read. The numbers live in the signed
# session cookie, so this prevents ordinary duplicate submissions only; it is not a
# server-side guarantee against someone replaying an older copy of the cookie.
FORM_NUMBERS_KEY = "citizen_account_form_numbers"
MAX_REMEMBERED_FORMS = 10
ALREADY_DONE_MESSAGE = (
    "This request was already completed, so nothing was changed. Activation codes are "
    "shown only once; choose Resend Activation if a new code is needed."
)


def staff_context():
    """Values every staff form page needs for the layout and its CSRF field."""
    return {
        "role_label": "LGU Staff", "staff_username": g.user["username"],
        "csrf_token": csrf_token(),
    }


def issue_form_number():
    """Create a one-time number for a form and remember it in the staff member's session."""
    number = token_urlsafe(16)
    remembered = session.get(FORM_NUMBERS_KEY, [])[-(MAX_REMEMBERED_FORMS - 1):]
    session[FORM_NUMBERS_KEY] = remembered + [number]
    return number


def use_form_number():
    """Accept the submitted form number once; any repeat or unknown number returns False."""
    remembered = session.get(FORM_NUMBERS_KEY, [])
    submitted = request.form.get("form_number", "")
    if submitted not in remembered:
        return False
    remembered.remove(submitted)
    session[FORM_NUMBERS_KEY] = remembered
    return True


def context_ids():
    """Read the batch and run the page was built from, as hidden numeric fields."""
    ids = []
    for key in ("batch_id", "run_id"):
        values = request.form.getlist(key)
        if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal() \
                or len(values[0]) > 18:
            abort(400, description="The form expired. Return to the Assessment page and try again.")
        ids.append(int(values[0]))
    return ids
