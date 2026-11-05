"""Describe document requests for staff pages, without any database access."""

from services.citizen_account_panel import PANEL_UNAVAILABLE

# Requests that are still in progress; only these can be cancelled.
OPEN_STATUSES = ("requested", "submitted", "needs_correction")

STATUS_LABELS = {
    "requested": "Waiting for upload", "submitted": "Ready for review",
    "needs_correction": "Needs correction", "accepted": "Accepted", "cancelled": "Cancelled",
}
# What the staff member should expect or do next, in plain words.
NEXT_STEPS = {
    "requested": "Waiting for the representative to upload this document.",
    "submitted": "The representative uploaded a file. It is ready for your review.",
    "needs_correction": "Waiting for the representative to upload a corrected file.",
    "accepted": "This document was accepted. No further action is needed.",
    "cancelled": "This request was cancelled.",
}
EVENT_LABELS = {
    "requested": "Document requested", "uploaded": "File uploaded", "accepted": "Accepted",
    "correction_requested": "Correction requested", "cancelled": "Request cancelled",
}


def present_request(row):
    """Turn a stored request row into what staff pages show."""
    return {
        "id": row["id"], "title": row["title"], "instructions": row["instructions"],
        "status": row["status"], "status_label": STATUS_LABELS[row["status"]],
        "next_step": NEXT_STEPS[row["status"]],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "can_cancel": row["status"] in OPEN_STATUSES,
    }


def present_document_requests(rows):
    """Describe the Document Requests section of the Assessment page."""
    if rows == PANEL_UNAVAILABLE:
        return {"state": "unavailable", "requests": []}
    return {"state": "ready", "requests": [present_request(row) for row in rows]}


def present_request_history(events):
    """Turn history rows (oldest first) into entries for the request page, newest first.

    Names are shown so staff can see who acted; no file names, paths or emails appear.
    """
    return [
        {
            "action_label": EVENT_LABELS[event["action"]],
            "actor_name": event["staff_name"] or event["citizen_name"],
            "created_at": event["created_at"], "note": event["note"],
        }
        for event in reversed(events)
    ]
