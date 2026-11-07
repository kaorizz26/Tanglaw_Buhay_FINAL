"""Shared first-run presentation for pages that require an active dataset."""

from flask import g, render_template

from services.auth import ADMIN_ROLE, csrf_token


NO_ACTIVE_DATASET = "active_batch_missing"


def is_no_active_dataset(error):
    """Recognize only the expected empty-installation condition."""
    return str(error) == NO_ACTIVE_DATASET


def render_dataset_setup(page_title, page_summary):
    """Show a useful empty state without treating first-run setup as an outage."""
    is_admin = g.user["role"] == ADMIN_ROLE
    return render_template(
        "staff/dataset_setup.html",
        page_title=page_title,
        page_summary=page_summary,
        is_admin=is_admin,
        role_label="System Administrator" if is_admin else "LGU Staff",
        staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )
