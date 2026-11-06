"""Append-only administrator batch-switch audit without household data."""


def record_switch(connection, actor_id, action, previous_batch_id,
                  previous_run_id, target_batch_id, target_run_id):
    """Record the checked zero-review transition in the caller's transaction."""
    return connection.execute(
        """INSERT INTO batch_activation_events
           (actor_id, action, previous_batch_id, previous_run_id,
            target_batch_id, target_run_id, active_reviews_at_switch)
           VALUES (%s, %s, %s, %s, %s, %s, 0) RETURNING id""",
        (actor_id, action, previous_batch_id, previous_run_id,
         target_batch_id, target_run_id),
    ).fetchone()[0]


def was_previously_active(connection, batch_id):
    """Identify a later return to an imported batch from immutable audit history."""
    return connection.execute(
        """SELECT 1 FROM batch_activation_events
           WHERE target_batch_id = %s OR previous_batch_id = %s LIMIT 1""",
        (batch_id, batch_id),
    ).fetchone() is not None
