-- Audit administrator downloads of privacy-limited staff-review action history.
-- Review records and assessment findings are unchanged.

CREATE TABLE staff_review_export_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    exported_by bigint NOT NULL REFERENCES staff_users (id),
    exported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    filters jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(filters) = 'object'),
    matching_actions integer NOT NULL CHECK (matching_actions >= 0),
    format text NOT NULL DEFAULT 'csv' CHECK (format = 'csv')
);

CREATE INDEX staff_review_export_events_actor_time
    ON staff_review_export_events (exported_by, exported_at DESC, id DESC);

CREATE FUNCTION validate_staff_review_export_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    actor_role text;
    actor_active boolean;
BEGIN
    SELECT role, is_active INTO actor_role, actor_active
    FROM staff_users WHERE id = NEW.exported_by;
    IF actor_role IS DISTINCT FROM 'system_administrator'
       OR actor_active IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Review activity exports require an active system administrator';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER staff_review_export_events_validate
    BEFORE INSERT ON staff_review_export_events
    FOR EACH ROW EXECUTE FUNCTION validate_staff_review_export_event();

CREATE FUNCTION prevent_staff_review_export_event_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Review activity export history is append-only';
END;
$$;

CREATE TRIGGER staff_review_export_events_immutable
    BEFORE UPDATE OR DELETE ON staff_review_export_events
    FOR EACH ROW EXECUTE FUNCTION prevent_staff_review_export_event_change();
