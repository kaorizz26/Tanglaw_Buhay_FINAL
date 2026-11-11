-- Staff forgot-password requests and administrator completion history.
-- Apply once, explicitly, after 0002_staff_authentication.sql.
-- The table stores no email address, password or password hash.

CREATE TABLE staff_password_reset_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    staff_id bigint NOT NULL REFERENCES staff_users (id),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'completed')),
    request_count integer NOT NULL DEFAULT 1
        CHECK (request_count BETWEEN 1 AND 100000),
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    completed_by bigint REFERENCES staff_users (id),
    delivery_method text CHECK (delivery_method IN ('email', 'direct_handoff')),
    -- Set only for email delivery. SMTP acceptance is not proof of inbox delivery.
    email_accepted boolean,
    CHECK (last_requested_at >= requested_at),
    CHECK (
        (status = 'pending' AND completed_at IS NULL AND completed_by IS NULL
         AND delivery_method IS NULL AND email_accepted IS NULL)
        OR
        (status = 'completed' AND completed_at IS NOT NULL AND completed_by IS NOT NULL
         AND delivery_method IS NOT NULL
         AND ((delivery_method = 'email' AND email_accepted IS NOT NULL)
              OR (delivery_method = 'direct_handoff' AND email_accepted IS NULL)))
    )
);

CREATE UNIQUE INDEX staff_password_reset_requests_one_pending
    ON staff_password_reset_requests (staff_id)
    WHERE status = 'pending';
CREATE INDEX staff_password_reset_requests_pending_order
    ON staff_password_reset_requests (status, last_requested_at DESC, id DESC);

CREATE FUNCTION enforce_staff_password_reset_request_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.staff_id <> NEW.staff_id OR OLD.requested_at <> NEW.requested_at THEN
        RAISE EXCEPTION 'Password reset request identity cannot change';
    END IF;

    IF OLD.status = 'pending' AND NEW.status = 'pending' THEN
        IF NEW.request_count < OLD.request_count
           OR NEW.last_requested_at < OLD.last_requested_at
           OR NEW.completed_at IS NOT NULL OR NEW.completed_by IS NOT NULL
           OR NEW.delivery_method IS NOT NULL OR NEW.email_accepted IS NOT NULL THEN
            RAISE EXCEPTION 'Only pending request activity may be refreshed';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'pending' AND NEW.status = 'completed' THEN
        IF NEW.request_count <> OLD.request_count
           OR NEW.last_requested_at <> OLD.last_requested_at THEN
            RAISE EXCEPTION 'Request activity cannot change during completion';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'completed' AND NEW.status = 'completed'
       AND OLD.delivery_method = 'email' AND OLD.email_accepted = false
       AND NEW.email_accepted = true
       AND NEW.request_count = OLD.request_count
       AND NEW.last_requested_at = OLD.last_requested_at
       AND NEW.completed_at = OLD.completed_at
       AND NEW.completed_by = OLD.completed_by
       AND NEW.delivery_method = OLD.delivery_method THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'Completed password reset requests are immutable';
END;
$$;

CREATE TRIGGER staff_password_reset_requests_guard
    BEFORE UPDATE ON staff_password_reset_requests
    FOR EACH ROW EXECUTE FUNCTION enforce_staff_password_reset_request_change();

CREATE FUNCTION prevent_staff_password_reset_request_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Password reset request history cannot be deleted';
END;
$$;

CREATE TRIGGER staff_password_reset_requests_no_delete
    BEFORE DELETE ON staff_password_reset_requests
    FOR EACH ROW EXECUTE FUNCTION prevent_staff_password_reset_request_delete();
