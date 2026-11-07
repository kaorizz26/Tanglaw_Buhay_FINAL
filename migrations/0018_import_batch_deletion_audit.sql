-- Preserve import accountability when an administrator permanently deletes an
-- unused inactive batch. Apply explicitly after migrations 0013 and 0015.

ALTER TABLE import_validation_events
    DROP CONSTRAINT import_validation_events_outcome_check,
    DROP CONSTRAINT import_validation_events_check;

ALTER TABLE import_validation_events
    ADD COLUMN deleted_batch_id bigint,
    ADD COLUMN deleted_at timestamptz,
    ADD COLUMN deleted_by bigint REFERENCES staff_users (id),
    ADD CONSTRAINT import_validation_events_outcome_check
        CHECK (outcome IN ('passed', 'failed', 'rejected', 'staged', 'deleted')),
    ADD CONSTRAINT import_validation_events_batch_state_check
        CHECK ((outcome = 'staged') = (batch_id IS NOT NULL)),
    ADD CONSTRAINT import_validation_events_deletion_audit_check
        CHECK (
            (outcome = 'deleted'
             AND deleted_batch_id IS NOT NULL
             AND deleted_at IS NOT NULL
             AND deleted_by IS NOT NULL
             AND batch_id IS NULL)
            OR
            (outcome <> 'deleted'
             AND deleted_batch_id IS NULL
             AND deleted_at IS NULL
             AND deleted_by IS NULL)
        );

CREATE UNIQUE INDEX import_validation_events_deleted_batch
    ON import_validation_events (deleted_batch_id)
    WHERE deleted_batch_id IS NOT NULL;
