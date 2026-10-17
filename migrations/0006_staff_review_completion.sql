-- Extend independent reviews; preserve existing reviews and assessment associations.
ALTER TABLE staff_reviews DROP CONSTRAINT staff_reviews_status_check;
ALTER TABLE staff_reviews
    ADD COLUMN closing_recommendation text,
    ADD COLUMN closing_explanation text,
    ADD COLUMN finished_by bigint REFERENCES staff_users (id),
    ADD COLUMN finished_at timestamptz,
    ADD COLUMN cancelled_by bigint REFERENCES staff_users (id),
    ADD COLUMN cancelled_at timestamptz,
    ADD COLUMN cancellation_reason text,
    ADD CONSTRAINT staff_reviews_status_check
        CHECK (status IN ('In Review', 'Closed', 'Cancelled')),
    ADD CONSTRAINT staff_reviews_completion_check CHECK (
        (status = 'In Review' AND closing_recommendation IS NULL
         AND closing_explanation IS NULL AND finished_by IS NULL AND finished_at IS NULL
         AND cancelled_by IS NULL AND cancelled_at IS NULL AND cancellation_reason IS NULL)
        OR (status = 'Closed' AND closing_recommendation IS NOT NULL
         AND closing_recommendation IN ('For LGU Follow-Up', 'No Further Action')
         AND closing_explanation IS NOT NULL
         AND char_length(btrim(closing_explanation)) BETWEEN 1 AND 2000
         AND finished_by IS NOT NULL AND finished_at IS NOT NULL
         AND cancelled_by IS NULL AND cancelled_at IS NULL AND cancellation_reason IS NULL)
        OR (status = 'Cancelled' AND closing_recommendation IS NULL
         AND closing_explanation IS NULL AND finished_by IS NULL AND finished_at IS NULL
         AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL
         AND (cancellation_reason IS NULL OR
              char_length(btrim(cancellation_reason)) BETWEEN 1 AND 1000))
    );

CREATE TABLE staff_review_notes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id bigint NOT NULL REFERENCES staff_reviews (id),
    body text NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 2000),
    authored_by bigint NOT NULL REFERENCES staff_users (id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX staff_review_notes_review ON staff_review_notes (review_id, created_at, id);
