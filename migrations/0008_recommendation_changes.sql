-- Apply explicitly after 0007. Original closing values are never rewritten.
CREATE TABLE staff_review_recommendation_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id bigint NOT NULL REFERENCES staff_reviews (id),
    sequence integer NOT NULL CHECK (sequence > 0),
    action text NOT NULL CHECK (action IN ('correct', 'withdraw')),
    previous_recommendation text NOT NULL
        CHECK (previous_recommendation IN ('For LGU Follow-Up', 'No Further Action')),
    previous_explanation text NOT NULL
        CHECK (char_length(btrim(previous_explanation)) BETWEEN 1 AND 2000),
    new_recommendation text,
    new_explanation text,
    reason text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 1000),
    changed_by bigint NOT NULL REFERENCES staff_users (id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (review_id, sequence),
    CHECK (
        (action = 'withdraw' AND new_recommendation IS NULL AND new_explanation IS NULL)
        OR (action = 'correct' AND new_recommendation IS NOT NULL
            AND new_recommendation IN ('For LGU Follow-Up', 'No Further Action')
            AND new_explanation IS NOT NULL
            AND char_length(btrim(new_explanation)) BETWEEN 1 AND 2000
            AND (new_recommendation <> previous_recommendation
                 OR new_explanation <> previous_explanation))
    )
);

CREATE FUNCTION prevent_recommendation_history_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Recommendation history is append-only';
END;
$$;
CREATE TRIGGER recommendation_history_immutable
    BEFORE UPDATE OR DELETE ON staff_review_recommendation_changes
    FOR EACH ROW EXECUTE FUNCTION prevent_recommendation_history_change();

-- One shared projection for the queue and all household review presentations.
CREATE VIEW staff_review_recommendation_state AS
SELECT r.id AS review_id,
       CASE WHEN change.id IS NULL THEN r.closing_recommendation
            ELSE change.new_recommendation END AS effective_recommendation,
       CASE WHEN change.id IS NULL THEN r.closing_explanation
            ELSE change.new_explanation END AS effective_explanation,
       COALESCE(change.changed_by, r.finished_by) AS effective_staff_id,
       COALESCE(change.created_at, r.finished_at) AS effective_at,
       COALESCE(change.sequence, 0) AS recommendation_version,
       COALESCE(change.action = 'withdraw', false) AS withdrawn
FROM staff_reviews r
LEFT JOIN LATERAL (
    SELECT c.* FROM staff_review_recommendation_changes c
    WHERE c.review_id = r.id ORDER BY c.sequence DESC LIMIT 1
) change ON true;
