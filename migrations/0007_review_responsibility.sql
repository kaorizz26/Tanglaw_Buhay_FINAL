-- Apply explicitly after 0006. Preserve every review and its original starter.
ALTER TABLE staff_reviews
    ADD COLUMN responsible_staff_id bigint REFERENCES staff_users (id),
    ADD COLUMN responsibility_version bigint NOT NULL DEFAULT 1
        CHECK (responsibility_version > 0);

UPDATE staff_reviews SET responsible_staff_id = started_by;
ALTER TABLE staff_reviews ALTER COLUMN responsible_staff_id SET NOT NULL;

CREATE TABLE staff_review_reassignments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id bigint NOT NULL REFERENCES staff_reviews (id),
    previous_staff_id bigint NOT NULL REFERENCES staff_users (id),
    responsible_staff_id bigint NOT NULL REFERENCES staff_users (id),
    changed_by bigint NOT NULL REFERENCES staff_users (id),
    reason text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 1000),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (previous_staff_id <> responsible_staff_id)
);
CREATE INDEX staff_review_reassignments_review
    ON staff_review_reassignments (review_id, created_at, id);
