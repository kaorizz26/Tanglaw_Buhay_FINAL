"""Admin-only batch activation with review guard and atomic metadata audit."""

import psycopg

from db import get_connection
from repositories import batch_activation_events, dataset_batches


class BatchActivationError(RuntimeError):
    """Fixed switch failure without private records or database details."""

    def __init__(self, code, *, review_count=0):
        super().__init__(code)
        self.review_count = review_count


def activation_overview(connection_factory=get_connection):
    """Return only prepared batch metadata and the current open-review count."""
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            batches = dataset_batches.list_batches_with_completed_selections(connection)
            active = [batch for batch in batches if batch["is_active"]]
            if len(active) != 1:
                raise BatchActivationError("active_context_unavailable")
            return {
                "active": active[0],
                "candidates": [batch for batch in batches if not batch["is_active"]],
                "in_progress_reviews": dataset_batches.count_in_progress_reviews(
                    connection, active[0]["id"],
                ),
            }
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
    """Switch one verified batch and record the transition in a single transaction."""
    values = (target_batch_id, actor_id, expected_active_batch_id,
              expected_active_run_id, expected_target_run_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise BatchActivationError("invalid_switch_request")
    try:
        with connection_factory() as connection, connection.transaction():
            current, target = dataset_batches.lock_switch_batches(
                connection, target_batch_id,
            )
            if (len(current) != 1 or current[0]["validation_status"] != "validated"
                    or target is None or target["validation_status"] != "validated"
                    or target["is_active"] or target_batch_id == current[0]["id"]):
                raise BatchActivationError("batch_not_switchable")
            actor = connection.execute(
                """SELECT role, is_active FROM staff_users WHERE id = %s FOR SHARE""",
                (actor_id,),
            ).fetchone()
            if actor != ("system_administrator", True):
                raise BatchActivationError("unauthorized")
            if current[0]["id"] != expected_active_batch_id:
                raise BatchActivationError("selection_changed")
            _require_selected_run(
                connection, current[0]["id"], expected_active_run_id,
            )
            _require_selected_run(connection, target_batch_id, expected_target_run_id)
            open_reviews = dataset_batches.count_in_progress_reviews(
                connection, current[0]["id"],
            )
            if open_reviews:
                raise BatchActivationError(
                    "unfinished_reviews", review_count=open_reviews,
                )
            source = target["source_provenance"].get("declared", {}).get("source")
            previously_active = batch_activation_events.was_previously_active(
                connection, target_batch_id,
            )
            action = (
                "activate" if source == "administrator_uploaded_2022_compatible_csv"
                and not previously_active else "switch_back"
            )
            dataset_batches.switch_prepared_batch(
                connection, current[0]["id"], target_batch_id,
                expected_target_run_id,
            )
            event_id = batch_activation_events.record_switch(
                connection, actor_id, action, current[0]["id"],
                expected_active_run_id, target_batch_id, expected_target_run_id,
            )
            selected = connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchall()
            if selected != [(target_batch_id,)]:
                raise BatchActivationError("switch_verification_failed")
        return {
            "action": action, "previous_batch_id": expected_active_batch_id,
            "target_batch_id": target_batch_id, "target_run_id": expected_target_run_id,
            "audit_event_id": event_id,
        }
    except BatchActivationError:
        raise
    except (psycopg.Error, RuntimeError, AttributeError, KeyError, TypeError):
        raise BatchActivationError("activation_unavailable") from None
