-- Append-only metadata audit for administrator batch switches.
-- Apply explicitly after 0004 and 0013. Batch and run history is retained.
CREATE TABLE batch_activation_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id bigint NOT NULL REFERENCES staff_users (id),
    switched_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    action text NOT NULL CHECK (action IN ('activate', 'switch_back')),
    previous_batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    previous_run_id bigint NOT NULL,
    target_batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    target_run_id bigint NOT NULL,
    active_reviews_at_switch bigint NOT NULL CHECK (active_reviews_at_switch = 0),
    FOREIGN KEY (previous_run_id, previous_batch_id)
        REFERENCES assessment_runs (id, batch_id),
    FOREIGN KEY (target_run_id, target_batch_id)
        REFERENCES assessment_runs (id, batch_id),
    CHECK (previous_batch_id <> target_batch_id)
);

CREATE INDEX batch_activation_events_recent
    ON batch_activation_events (switched_at DESC, id DESC);
