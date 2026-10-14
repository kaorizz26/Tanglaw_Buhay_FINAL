-- Explicit approved-result selection; no source-table or historical-run changes.
-- Apply only in the approved transaction that persists/designates a new version.
CREATE TABLE assessment_run_selections (
    batch_id bigint PRIMARY KEY REFERENCES dataset_batches (id),
    run_id bigint NOT NULL,
    selected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id, batch_id) REFERENCES assessment_runs (id, batch_id)
);

-- The repository permits designation only for a validated batch/completed run.
-- Readers additionally require completed status, even after privileged SQL bypass.
