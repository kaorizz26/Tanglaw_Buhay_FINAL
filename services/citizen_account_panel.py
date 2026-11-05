"""Describe a household's Citizen Account for the Assessment page, without any database access."""

# Stands in for the account row when the account tables are not installed.
PANEL_UNAVAILABLE = "unavailable"

STATUS_LABELS = {
    "pending_activation": "Pending Activation", "active": "Active",
    "deactivated": "Deactivated",
}


def present_account_panel(row):
    """Turn a stored account row (or a missing one) into what the Assessment page shows."""
    if row == PANEL_UNAVAILABLE:
        return {"state": "unavailable"}
    if row is None:
        return {"state": "none"}
    return {
        "state": row["status"], "status_label": STATUS_LABELS[row["status"]],
        "name": row["representative_name"], "email": row["email"],
        "created_at": row["created_at"], "activated_at": row["activated_at"],
        "email_accepted_at": row["email_accepted_at"],
        "link_valid": row["link_valid"], "expires_at": row["expires_at"],
        "can_resend": row["status"] == "pending_activation",
    }
