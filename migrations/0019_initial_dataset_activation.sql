-- Allow an administrator-prepared batch to become the first active dataset.
-- Existing switch audit rows keep their required previous batch and run.

ALTER TABLE batch_activation_events
    DROP CONSTRAINT batch_activation_events_action_check,
    ALTER COLUMN previous_batch_id DROP NOT NULL,
    ALTER COLUMN previous_run_id DROP NOT NULL;

ALTER TABLE batch_activation_events
    ADD CONSTRAINT batch_activation_events_action_check
        CHECK (action IN ('initial_activate', 'activate', 'switch_back')),
    ADD CONSTRAINT batch_activation_events_initial_shape_check
        CHECK (
            (action = 'initial_activate'
             AND previous_batch_id IS NULL
             AND previous_run_id IS NULL)
            OR
            (action <> 'initial_activate'
             AND previous_batch_id IS NOT NULL
             AND previous_run_id IS NOT NULL)
        );
