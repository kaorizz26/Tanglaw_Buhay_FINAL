"""Admin-only batch activation with review guard and atomic metadata audit."""

import psycopg

from db import get_connection
from repositories import batch_activation_events, dataset_batches
from services.import_sources import ADMIN_UPLOAD_SOURCES


class BatchActivationError(RuntimeError):
    """Fixed switch failure without private records or database details."""

    def __init__(self, code, *, review_count=0, account_count=0, request_count=0):
        super().__init__(code)
        self.review_count = review_count
        self.account_count = account_count
        self.request_count = request_count


def describe_citizen_work(account_count, request_count):
    """Say in plain words which citizen records stop a batch switch."""
    parts = []
    if account_count:
        noun = "citizen account" if account_count == 1 else "citizen accounts"
        parts.append(f"{account_count} {noun} that "
                     f"{'is' if account_count == 1 else 'are'} active or waiting for activation")
    if request_count:
        noun = "document request" if request_count == 1 else "document requests"
        parts.append(f"{request_count} unfinished {noun}")
    return " and ".join(parts)


def activation_overview(connection_factory=get_connection):
    """Return only prepared batch metadata and the current open-review count."""
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            batches = dataset_batches.list_batches_with_completed_selections(connection)
            active_batches = dataset_batches.find_active_batches(connection)
            if len(active_batches) > 1:
                raise BatchActivationError("active_context_unavailable")
            active = None
            if active_batches:
                active = next(
                    (batch for batch in batches
                     if batch["id"] == active_batches[0]["id"]),
                    None,
                )
                if active is None:
                    raise BatchActivationError("active_context_unavailable")
            citizen_work = (
                dataset_batches.count_blocking_citizen_work(connection, active["id"])
                if active else {"accounts": 0, "requests": 0}
            )
            return {
                "active": active,
                "candidates": [batch for batch in batches if not batch["is_active"]],
                "initial_setup": active is None,
                "in_progress_reviews": (
                    dataset_batches.count_in_progress_reviews(connection, active["id"])
                    if active else 0
                ),
                "citizen_accounts": citizen_work["accounts"],
                "open_document_requests": citizen_work["requests"],
                "citizen_work_summary": describe_citizen_work(
                    citizen_work["accounts"], citizen_work["requests"],
                ),
            }
    except BatchActivationError:
        raise
    except (psycopg.Error, RuntimeError):
        raise BatchActivationError("activation_overview_unavailable") from None


def active_dataset_exists(connection_factory=get_connection):
    """Return whether exactly one active dataset exists."""
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            active = dataset_batches.find_active_batches(connection)
            if len(active) > 1:
                raise BatchActivationError("active_context_unavailable")
            return len(active) == 1
    except BatchActivationError:
        raise
    except (psycopg.Error, RuntimeError):
        raise BatchActivationError("activation_overview_unavailable") from None


def _require_selected_run(connection, batch_id, expected_run_id):
    """Require a complete designated run and unchanged source coverage."""
    selected = dataset_batches.lock_designated_completed_run(connection, batch_id)
    if selected is None or selected["run_id"] != expected_run_id:
        raise BatchActivationError("selection_changed")
    source_count, stored_count = dataset_batches.count_completed_run_coverage(
        connection, batch_id, expected_run_id,
    )
    if (source_count <= 0 or source_count != selected["households"]
            or stored_count != source_count):
        raise BatchActivationError("batch_not_ready")
    return selected


def activate_prepared_batch(target_batch_id, actor_id, *, expected_active_batch_id,
                            expected_active_run_id, expected_target_run_id,
                            connection_factory=get_connection):
    """Activate the first prepared batch or switch between prepared batches."""
    values = (target_batch_id, actor_id, expected_target_run_id)
    initial_request = (
        expected_active_batch_id is None and expected_active_run_id is None
    )
    switch_request = (
        type(expected_active_batch_id) is int and expected_active_batch_id > 0
        and type(expected_active_run_id) is int and expected_active_run_id > 0
    )
    if (any(type(value) is not int or value <= 0 for value in values)
            or not (initial_request or switch_request)):
        raise BatchActivationError("invalid_switch_request")
    try:
        with connection_factory() as connection, connection.transaction():
            current, target = dataset_batches.lock_switch_batches(
                connection, target_batch_id,
            )
            if (len(current) > 1 or target is None
                    or target["validation_status"] != "validated"
                    or target["is_active"]):
                raise BatchActivationError("batch_not_switchable")
            actor = connection.execute(
                """SELECT role, is_active FROM staff_users WHERE id = %s FOR SHARE""",
                (actor_id,),
            ).fetchone()
            if actor != ("system_administrator", True):
                raise BatchActivationError("unauthorized")
            if bool(current) == initial_request:
                raise BatchActivationError("selection_changed")
            if current and (
                    current[0]["validation_status"] != "validated"
                    or current[0]["id"] != expected_active_batch_id
                    or target_batch_id == current[0]["id"]):
                raise BatchActivationError("selection_changed")
            if current:
                _require_selected_run(
                    connection, current[0]["id"], expected_active_run_id,
                )
            _require_selected_run(connection, target_batch_id, expected_target_run_id)
            open_reviews = (
                dataset_batches.count_in_progress_reviews(connection, current[0]["id"])
                if current else 0
            )
            if open_reviews:
                raise BatchActivationError(
                    "unfinished_reviews", review_count=open_reviews,
                )
            # Temporary rule until Citizen Accounts can follow a household across survey
            # years. The batch row is already locked for update, and every staff action
            # that adds an account or request first holds that row for share, so nothing
            # can slip in between this count and the switch.
            citizen_work = (
                dataset_batches.count_blocking_citizen_work(connection, current[0]["id"])
                if current else {"accounts": 0, "requests": 0}
            )
            if citizen_work["accounts"] or citizen_work["requests"]:
                raise BatchActivationError(
                    "citizen_work_open", account_count=citizen_work["accounts"],
                    request_count=citizen_work["requests"],
                )
            source = target["source_provenance"].get("declared", {}).get("source")
            if not current and source not in ADMIN_UPLOAD_SOURCES:
                raise BatchActivationError("batch_not_switchable")
            previously_active = batch_activation_events.was_previously_active(
                connection, target_batch_id,
            )
            action = (
                "initial_activate" if not current else
                "activate" if source in ADMIN_UPLOAD_SOURCES and not previously_active
                else "switch_back"
            )
            if current:
                dataset_batches.switch_prepared_batch(
                    connection, current[0]["id"], target_batch_id,
                    expected_target_run_id,
                )
            else:
                dataset_batches.activate_initial_prepared_batch(
                    connection, target_batch_id, expected_target_run_id,
                )
            event_id = batch_activation_events.record_switch(
                connection, actor_id, action,
                current[0]["id"] if current else None,
                expected_active_run_id if current else None,
                target_batch_id, expected_target_run_id,
            )
            selected = connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchall()
            if selected != [(target_batch_id,)]:
                raise BatchActivationError("switch_verification_failed")
        return {
            "action": action,
            "previous_batch_id": expected_active_batch_id if current else None,
            "target_batch_id": target_batch_id, "target_run_id": expected_target_run_id,
            "audit_event_id": event_id,
        }
    except BatchActivationError:
        raise
    except (psycopg.Error, RuntimeError, AttributeError, KeyError, TypeError):
        raise BatchActivationError("activation_unavailable") from None
