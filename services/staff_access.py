"""Validated, audited access management for individual LGU Staff accounts."""

from db import get_connection
from repositories import staff_permissions, staff_users


DASHBOARD_PERMISSION = "dashboard.view"
PERMISSION_CODES = frozenset({
    DASHBOARD_PERMISSION,
    "households.view",
    "assessments.view",
    "reviews.view",
    "reviews.manage",
    "submissions.view",
    "submissions.manage",
})
PERMISSION_DEPENDENCIES = {
    "reviews.view": frozenset({"assessments.view"}),
    "reviews.manage": frozenset({"reviews.view"}),
    "submissions.manage": frozenset({"submissions.view"}),
}
ACCESS_PRESETS = {
    "dashboard_only": frozenset({DASHBOARD_PERMISSION}),
    "household_viewer": frozenset({DASHBOARD_PERMISSION, "households.view"}),
    "assessment_viewer": frozenset({
        DASHBOARD_PERMISSION, "households.view", "assessments.view",
    }),
    "case_reviewer": frozenset({
        DASHBOARD_PERMISSION, "households.view", "assessments.view",
        "reviews.view", "reviews.manage",
    }),
    "submissions_officer": frozenset({
        DASHBOARD_PERMISSION, "households.view", "assessments.view",
        "submissions.view", "submissions.manage",
    }),
    "select_all": PERMISSION_CODES,
}


class StaffAccessInputError(ValueError):
    """A requested account or permission selection is invalid."""


def _valid_id(value):
    return type(value) is int and 0 < value <= 9223372036854775807


def _validate_catalog(catalog):
    codes = {item["code"] for item in catalog}
    if codes != PERMISSION_CODES:
        raise RuntimeError("Staff access permissions are not configured correctly.")


def validate_permission_selection(permission_codes):
    """Return a safe immutable selection or explain the violated access rule."""
    if isinstance(permission_codes, (str, bytes)):
        raise StaffAccessInputError("Choose valid staff permissions.")
    try:
        submitted = list(permission_codes)
    except TypeError:
        raise StaffAccessInputError("Choose valid staff permissions.") from None
    if any(not isinstance(code, str) for code in submitted):
        raise StaffAccessInputError("Choose valid staff permissions.")
    selected = frozenset(submitted)
    if len(selected) != len(submitted) or not selected <= PERMISSION_CODES:
        raise StaffAccessInputError("Choose valid staff permissions.")
    if DASHBOARD_PERMISSION not in selected:
        raise StaffAccessInputError("Dashboard access is required for every staff account.")
    for permission, required in PERMISSION_DEPENDENCIES.items():
        if permission in selected and not required <= selected:
            labels = {
                "reviews.view": "Assessment Results",
                "reviews.manage": "Staff Review History",
                "submissions.manage": "Submissions",
            }
            raise StaffAccessInputError(
                f"{permission} requires {labels[permission]} access."
            )
    return selected


def _access_result(account, catalog, selected, *, changed=False):
    permissions = [
        {**item, "granted": item["code"] in selected}
        for item in catalog
    ]
    return {
        "account": account,
        "permissions": permissions,
        "selected": frozenset(selected),
        "changed": changed,
    }


def get_staff_access(staff_id, *, connection_factory=get_connection):
    """Return one staff account and its current permission selection."""
    if not _valid_id(staff_id):
        raise StaffAccessInputError("Choose a valid staff account.")
    with connection_factory() as connection:
        account = staff_permissions.find_staff_target(connection, staff_id)
        if not account:
            raise StaffAccessInputError("This staff account is unavailable.")
        catalog = staff_permissions.list_catalog(connection)
        _validate_catalog(catalog)
        selected = validate_permission_selection(
            staff_permissions.list_codes(connection, staff_id),
        )
    return _access_result(account, catalog, selected)


def update_staff_access(administrator_id, staff_id, permission_codes,
                        *, connection_factory=get_connection):
    """Replace one active staff account's grants, audit changes and revoke sessions."""
    if not _valid_id(administrator_id) or not _valid_id(staff_id):
        raise StaffAccessInputError("Choose a valid administrator and staff account.")
    selected = validate_permission_selection(permission_codes)
    with connection_factory() as connection:
        with connection.transaction():
            administrator = staff_permissions.find_active_administrator(
                connection, administrator_id,
            )
            if not administrator:
                raise StaffAccessInputError("Your administrator sign-in has changed. Sign in again.")
            account = staff_permissions.find_staff_target(connection, staff_id, lock=True)
            if not account or not account["is_active"]:
                raise StaffAccessInputError("Only an active staff account can have access changed.")
            catalog = staff_permissions.list_catalog(connection)
            _validate_catalog(catalog)
            current = frozenset(staff_permissions.list_codes(
                connection, staff_id, lock=True,
            ))
            added = selected - current
            removed = current - selected
            if not added and not removed:
                return _access_result(account, catalog, current)

            staff_permissions.remove_grants(connection, staff_id, removed)
            staff_permissions.add_grants(
                connection, staff_id, added, administrator_id,
            )
            staff_permissions.record_events(
                connection, staff_id, administrator_id, removed, "revoked",
            )
            staff_permissions.record_events(
                connection, staff_id, administrator_id, added, "granted",
            )
            staff_users.invalidate_sessions(connection, staff_id)
            account["session_version"] += 1
            return _access_result(account, catalog, selected, changed=True)


def get_staff_access_history(staff_id, *, limit=50, connection_factory=get_connection):
    """Return recent administrator access changes for one staff account."""
    if not _valid_id(staff_id) or type(limit) is not int or not 1 <= limit <= 200:
        raise StaffAccessInputError("Choose a valid staff account and history limit.")
    with connection_factory() as connection:
        account = staff_permissions.find_staff_target(connection, staff_id)
        if not account:
            raise StaffAccessInputError("This staff account is unavailable.")
        events = staff_permissions.list_events(connection, staff_id, limit=limit)
    return {"account": account, "events": events}
