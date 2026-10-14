-- Assessment storage only. Apply explicitly after migration 0001.
-- No source-table changes, year-specific assumptions, or automatic calculations.

CREATE TABLE assessment_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    calculation_version text NOT NULL CHECK (btrim(calculation_version) <> ''),
    calculation_provenance jsonb NOT NULL
        CHECK (jsonb_typeof(calculation_provenance) = 'object'
               AND calculation_provenance <> '{}'::jsonb),
    expected_household_count bigint NOT NULL CHECK (expected_household_count > 0),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at timestamptz,
    UNIQUE (id, batch_id),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);

CREATE INDEX assessment_runs_batch ON assessment_runs (batch_id, id);

CREATE TABLE household_assessments (
    run_id bigint NOT NULL,
    batch_id bigint NOT NULL,
    household_uuid text NOT NULL CHECK (btrim(household_uuid) <> ''),
    findings jsonb NOT NULL,
    -- The indexed projection comes from the stored result; it cannot drift.
    vulnerability_rank bigint GENERATED ALWAYS AS
        ((findings #>> '{mpi,vulnerability_rank}')::bigint) STORED
        NOT NULL CHECK (vulnerability_rank > 0),
    PRIMARY KEY (run_id, household_uuid),
    FOREIGN KEY (run_id, batch_id) REFERENCES assessment_runs (id, batch_id),
    FOREIGN KEY (batch_id, household_uuid) REFERENCES households (batch_id, uuid),
    CHECK ((jsonb_typeof(findings) = 'object'
        AND findings ->> 'household_uuid' = household_uuid
        AND jsonb_typeof(findings -> 'survey_year') = 'number'
        AND jsonb_typeof(findings -> 'mpi') = 'object'
        AND findings #>> '{mpi,uuid}' = household_uuid
        AND jsonb_typeof(findings -> 'programs') = 'object'
        AND (findings -> 'programs') ?& ARRAY['4Ps', 'SLP', 'SPISC', 'TUPAD']
        AND ((findings -> 'programs') - ARRAY['4Ps', 'SLP', 'SPISC', 'TUPAD']) = '{}'::jsonb
    ) IS TRUE)
);

CREATE INDEX household_assessments_rank
    ON household_assessments (run_id, vulnerability_rank, household_uuid);
