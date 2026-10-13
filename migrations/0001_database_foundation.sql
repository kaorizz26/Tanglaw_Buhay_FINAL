-- Phase 6B.1: batch-scoped storage for the finalized 2022 Pozorrubio sources.
-- Apply once to an empty database with psql -1 -v ON_ERROR_STOP=1 -f <this file>.
-- The -1 flag wraps the whole file in a transaction and rolls back on error.

CREATE TABLE dataset_batches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_year integer NOT NULL CHECK (survey_year = 2022),
    municipality text NOT NULL CHECK (municipality = 'Pozorrubio'),
    -- Dataset name -> ordered column-name array. JSONB row objects do not retain key order.
    source_columns jsonb NOT NULL CHECK (jsonb_typeof(source_columns) = 'object'),
    -- File identity and documented synthetic/pilot provenance belong to the batch.
    source_provenance jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(source_provenance) = 'object'),
    validation_status text NOT NULL DEFAULT 'pending'
        CHECK (validation_status IN ('pending', 'validated', 'failed')),
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT is_active OR validation_status = 'validated')
);

CREATE UNIQUE INDEX dataset_batches_one_active
    ON dataset_batches (is_active) WHERE is_active;

CREATE TABLE households (
    batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    uuid text NOT NULL CHECK (btrim(uuid) <> ''),
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, uuid),
    UNIQUE (batch_id, source_row_number)
);

CREATE TABLE household_members (
    batch_id bigint NOT NULL,
    uuid text NOT NULL,
    line_number integer NOT NULL,
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, uuid, line_number),
    UNIQUE (batch_id, source_row_number),
    FOREIGN KEY (batch_id, uuid) REFERENCES households (batch_id, uuid)
);

CREATE TABLE member_health (
    batch_id bigint NOT NULL,
    uuid text NOT NULL,
    section_l_line_number integer NOT NULL,
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, uuid, section_l_line_number),
    UNIQUE (batch_id, source_row_number),
    FOREIGN KEY (batch_id, uuid, section_l_line_number)
        REFERENCES household_members (batch_id, uuid, line_number)
);

CREATE TABLE child_death_records (
    batch_id bigint NOT NULL,
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    uuid text NOT NULL,
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, source_row_number),
    FOREIGN KEY (batch_id, uuid) REFERENCES households (batch_id, uuid)
);

-- Reference CSV values remain strings, including empty strings and literal "NA".
-- Their original column order is held in dataset_batches.source_columns.
CREATE TABLE codebook_values (
    batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE mpi_code_mappings (
    batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, source_row_number)
);

CREATE TABLE variable_definitions (
    batch_id bigint NOT NULL REFERENCES dataset_batches (id),
    source_row_number bigint NOT NULL CHECK (source_row_number > 0),
    responses jsonb NOT NULL CHECK (jsonb_typeof(responses) = 'object'),
    PRIMARY KEY (batch_id, source_row_number)
);
