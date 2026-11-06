-- Metadata-only audit for administrator CSV validation attempts.
-- Apply explicitly after staff authentication migration 0002.
CREATE TABLE import_validation_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id bigint NOT NULL REFERENCES staff_users (id),
    attempted_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome text NOT NULL CHECK (outcome IN ('passed', 'failed', 'rejected', 'staged')),
    batch_id bigint UNIQUE REFERENCES dataset_batches (id),
    reference_batch_id bigint REFERENCES dataset_batches (id),
    reference_version text,
    file_metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(file_metadata) = 'object'),
    error_count integer NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    warning_count integer NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    CHECK ((outcome = 'staged') = (batch_id IS NOT NULL))
);

CREATE INDEX import_validation_events_retention
    ON import_validation_events (attempted_at);
