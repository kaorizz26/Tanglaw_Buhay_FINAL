-- Apply explicitly after migrations 0001-0004. No source or finding changes.
CREATE TABLE staff_reviews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL,
    household_uuid text NOT NULL,
    assessment_run_id bigint NOT NULL,
    program text NOT NULL CHECK (program IN ('4Ps', 'SLP', 'SPISC', 'TUPAD')),
    status text NOT NULL DEFAULT 'In Review' CHECK (status IN ('In Review')),
    starting_reason text CHECK (
        starting_reason IS NULL OR
        (char_length(btrim(starting_reason)) BETWEEN 1 AND 1000)
    ),
    started_by bigint NOT NULL REFERENCES staff_users (id),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id, household_uuid) REFERENCES households (batch_id, uuid),
    FOREIGN KEY (assessment_run_id, household_uuid)
        REFERENCES household_assessments (run_id, household_uuid),
    FOREIGN KEY (assessment_run_id, batch_id) REFERENCES assessment_runs (id, batch_id)
);

-- Concurrent requests cannot open duplicate active household/program reviews.
-- A later migration can extend statuses without replacing these review records.
CREATE UNIQUE INDEX staff_reviews_one_active
    ON staff_reviews (batch_id, household_uuid, program) WHERE status = 'In Review';
