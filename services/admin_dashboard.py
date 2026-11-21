"""Aggregate operational summaries shown only on the Administrator dashboard."""

from collections import Counter

import psycopg

from db import get_connection
from repositories import dataset_batches


class AdminDashboardSummaryError(RuntimeError):
    """An aggregate dashboard summary could not be read safely."""


def get_import_dashboard_summary(*, connection_factory=get_connection):
    """Return stored dataset readiness and survey-year counts in one snapshot."""
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            )
            batches = [
                dict(batch)
                for batch in dataset_batches.list_admin_dashboard_batches(connection)
            ]
    except (psycopg.Error, RuntimeError):
        raise AdminDashboardSummaryError("import_dashboard_unavailable") from None

    active = [batch for batch in batches if batch["is_active"]]
    if len(active) > 1:
        raise AdminDashboardSummaryError("import_dashboard_unavailable")
    status_counts = {
        "active": len(active),
        "ready": sum(
            not batch["is_active"] and batch["run_id"] is not None
            for batch in batches
        ),
        "awaiting": sum(
            not batch["is_active"] and batch["run_id"] is None
            for batch in batches
        ),
    }
    if sum(status_counts.values()) != len(batches):
        raise AdminDashboardSummaryError("import_dashboard_count_mismatch")
    years = Counter(batch["survey_year"] for batch in batches)
    return {
        "active": active[0] if active else None,
        "statuses": [
            {"key": "active", "label": "Active", "count": status_counts["active"]},
            {"key": "ready", "label": "Ready to activate", "count": status_counts["ready"]},
            {"key": "awaiting", "label": "Awaiting preparation", "count": status_counts["awaiting"]},
        ],
        "years": [
            {"year": year, "count": count}
            for year, count in sorted(years.items(), reverse=True)
        ],
        "total": len(batches),
    }
