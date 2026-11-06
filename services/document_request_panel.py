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


def _size_label(size_bytes):
    """Show a file size in plain units, for example '340 KB' or '1.2 MB'."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size_bytes / 1024))} KB"


def present_files(files, status):
    """List uploaded versions (newest first) and mark the one staff should review.

    Only the newest version can be reviewed, and only while the request is Ready for
    Review. Older versions stay listed so nothing the representative sent is lost.
    """
    return [
        {
            "id": item["id"], "version_number": item["version_number"],
            "display_name": item["display_name"], "size_label": _size_label(item["size_bytes"]),
            "uploaded_at": item["uploaded_at"], "is_latest": position == 0,
            "awaiting_review": position == 0 and status == "submitted",
        }
        for position, item in enumerate(files)
    ]


def present_request(row):
    """Turn a stored request row into what staff pages show."""
    return {
        "id": row["id"], "title": row["title"], "instructions": row["instructions"],
        "status": row["status"], "status_label": STATUS_LABELS[row["status"]],
        "next_step": NEXT_STEPS[row["status"]],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
        "can_cancel": row["status"] in OPEN_STATUSES,
        "can_review": row["status"] == "submitted",
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
