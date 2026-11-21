-- Per-account LGU Staff access. Administrators continue to use their role-based access.
-- Existing staff keep their current access; future staff start with Dashboard only.

CREATE TABLE staff_permissions (
    code text PRIMARY KEY
        CHECK (code ~ '^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$'),
    label text NOT NULL UNIQUE CHECK (char_length(btrim(label)) BETWEEN 1 AND 80),
    description text NOT NULL CHECK (char_length(btrim(description)) BETWEEN 1 AND 240),
    sort_order smallint NOT NULL UNIQUE CHECK (sort_order > 0)
);

INSERT INTO staff_permissions (code, label, description, sort_order) VALUES
    ('dashboard.view', 'Dashboard', 'Open the LGU Staff dashboard.', 10),
    ('households.view', 'Household Records', 'Browse and open household source records.', 20),
    ('assessments.view', 'Assessment Results', 'View prioritization, assessment findings, evidence and progression.', 30),
    ('reviews.view', 'Staff Review History', 'View the Review Queue and saved staff-review history.', 40),
    ('reviews.manage', 'Manage Staff Reviews', 'Start and update staff reviews and recommendations.', 50),
    ('submissions.view', 'Submissions', 'View document requests, uploaded files and Citizen Accounts.', 60),
    ('submissions.manage', 'Manage Submissions', 'Request and review documents and manage Citizen Accounts.', 70);

CREATE TABLE staff_user_permissions (
    staff_id bigint NOT NULL REFERENCES staff_users (id),
    permission_code text NOT NULL REFERENCES staff_permissions (code),
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    granted_by bigint REFERENCES staff_users (id),
    grant_source text NOT NULL
        CHECK (grant_source IN ('migration', 'account_default', 'administrator')),
    PRIMARY KEY (staff_id, permission_code),
    CHECK (
        (grant_source = 'administrator' AND granted_by IS NOT NULL)
        OR (grant_source IN ('migration', 'account_default') AND granted_by IS NULL)
    )
);

CREATE INDEX staff_user_permissions_permission
    ON staff_user_permissions (permission_code, staff_id);

CREATE FUNCTION validate_staff_permission_grant() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_role text;
    actor_role text;
    actor_active boolean;
BEGIN
    SELECT role INTO target_role FROM staff_users WHERE id = NEW.staff_id;
    IF target_role IS DISTINCT FROM 'lgu_staff' THEN
        RAISE EXCEPTION 'Permissions may only be assigned to LGU Staff accounts';
    END IF;

    IF NEW.grant_source = 'administrator' THEN
        SELECT role, is_active INTO actor_role, actor_active
        FROM staff_users WHERE id = NEW.granted_by;
        IF actor_role IS DISTINCT FROM 'system_administrator' OR actor_active IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'Permission changes require an active system administrator';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER staff_user_permissions_validate
    BEFORE INSERT OR UPDATE ON staff_user_permissions
    FOR EACH ROW EXECUTE FUNCTION validate_staff_permission_grant();

-- Preserve the access existing staff already had before permission management.
INSERT INTO staff_user_permissions
    (staff_id, permission_code, granted_by, grant_source)
SELECT users.id, permissions.code, NULL, 'migration'
FROM staff_users AS users
CROSS JOIN staff_permissions AS permissions
WHERE users.role = 'lgu_staff';

CREATE FUNCTION grant_new_staff_dashboard_access() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.role = 'lgu_staff' THEN
        INSERT INTO staff_user_permissions
            (staff_id, permission_code, granted_by, grant_source)
        VALUES (NEW.id, 'dashboard.view', NULL, 'account_default');
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER staff_users_default_access
    AFTER INSERT ON staff_users
    FOR EACH ROW EXECUTE FUNCTION grant_new_staff_dashboard_access();

CREATE FUNCTION enforce_staff_permission_dependencies() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_id bigint := COALESCE(NEW.staff_id, OLD.staff_id);
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM staff_users
        WHERE id = target_id AND role = 'lgu_staff'
    ) THEN
        RETURN NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'dashboard.view'
    ) THEN
        RAISE EXCEPTION 'LGU Staff must retain Dashboard access';
    END IF;

    IF EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'reviews.view'
    ) AND NOT EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'assessments.view'
    ) THEN
        RAISE EXCEPTION 'Staff Review History requires Assessment Results access';
    END IF;

    IF EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'reviews.manage'
    ) AND NOT EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'reviews.view'
    ) THEN
        RAISE EXCEPTION 'Manage Staff Reviews requires Staff Review History access';
    END IF;

    IF EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'submissions.manage'
    ) AND NOT EXISTS (
        SELECT 1 FROM staff_user_permissions
        WHERE staff_id = target_id AND permission_code = 'submissions.view'
    ) THEN
        RAISE EXCEPTION 'Manage Submissions requires Submissions access';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER staff_user_permissions_dependencies
    AFTER INSERT OR UPDATE OR DELETE ON staff_user_permissions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION enforce_staff_permission_dependencies();

CREATE TABLE staff_permission_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    staff_id bigint NOT NULL REFERENCES staff_users (id),
    permission_code text NOT NULL REFERENCES staff_permissions (code),
    action text NOT NULL CHECK (action IN ('granted', 'revoked')),
    changed_by bigint NOT NULL REFERENCES staff_users (id),
    changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX staff_permission_events_staff_history
    ON staff_permission_events (staff_id, changed_at DESC, id DESC);

CREATE FUNCTION validate_staff_permission_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    target_role text;
    actor_role text;
    actor_active boolean;
BEGIN
    SELECT role INTO target_role FROM staff_users WHERE id = NEW.staff_id;
    SELECT role, is_active INTO actor_role, actor_active
    FROM staff_users WHERE id = NEW.changed_by;
    IF target_role IS DISTINCT FROM 'lgu_staff' THEN
        RAISE EXCEPTION 'Permission history requires an LGU Staff target';
    END IF;
    IF actor_role IS DISTINCT FROM 'system_administrator' OR actor_active IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'Permission history requires an active system administrator';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER staff_permission_events_validate
    BEFORE INSERT ON staff_permission_events
    FOR EACH ROW EXECUTE FUNCTION validate_staff_permission_event();

CREATE FUNCTION prevent_staff_permission_event_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Staff permission history is append-only';
END;
$$;

CREATE TRIGGER staff_permission_events_immutable
    BEFORE UPDATE OR DELETE ON staff_permission_events
    FOR EACH ROW EXECUTE FUNCTION prevent_staff_permission_event_change();
