-- Digital Dropbox: document requests, uploaded file versions and an append-only history.
-- Apply once, explicitly, after migration 0014 (citizen accounts) and 0002 (staff_users).
-- It does not depend on 0013 (Data Import) and changes no existing table.
-- Document tables never reference assessments, subsidy findings or Staff Reviews, so no
-- document action can change them. The file contents are NOT stored here: only a random
-- storage key that points into the private storage folder (see DEVELOPER_GUIDE.md).

-- One row per requested document. The household is reached through the citizen account.
CREATE TABLE document_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES citizen_accounts (id),
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 3 AND 120),
    instructions text
        CHECK (instructions IS NULL OR char_length(btrim(instructions)) BETWEEN 1 AND 500),
    status text NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested', 'submitted', 'needs_correction', 'accepted', 'cancelled')),
    requested_by bigint NOT NULL REFERENCES staff_users (id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Lets history rows prove a citizen only acts on a request of their own account.
    UNIQUE (id, account_id)
);

CREATE INDEX document_requests_account ON document_requests (account_id, created_at, id);
CREATE INDEX document_requests_status ON document_requests (status, updated_at);

-- Every upload is a new version; earlier versions are kept.
CREATE TABLE document_files (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES document_requests (id),
    version_number integer NOT NULL CHECK (version_number BETWEEN 1 AND 10),
    -- The citizen's file name, cleaned, for display only. It is never used as a path.
    display_name text NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 100),
    content_type text NOT NULL
        CHECK (content_type IN ('application/pdf', 'image/jpeg', 'image/png')),
    size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 5242880),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- Random name of the stored file, e.g. 3f2a...c9.pdf.
    storage_key text NOT NULL UNIQUE CHECK (storage_key ~ '^[0-9a-f]{32}[.](pdf|jpg|png)$'),
    uploaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (request_id, version_number),
    UNIQUE (id, request_id)
);

CREATE TABLE document_request_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES document_requests (id),
    action text NOT NULL CHECK (action IN (
        'requested', 'uploaded', 'accepted', 'correction_requested', 'cancelled')),
    -- Exactly one actor: a staff member, or the citizen account that uploaded.
    staff_id bigint REFERENCES staff_users (id),
    account_id bigint,
    file_id bigint,
    -- A correction reason or cancellation reason. Both are shown to the citizen.
    note text CHECK (note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (file_id, request_id) REFERENCES document_files (id, request_id),
    FOREIGN KEY (request_id, account_id) REFERENCES document_requests (id, account_id),
    CHECK ((staff_id IS NULL) <> (account_id IS NULL)),
    CHECK ((action = 'uploaded') = (account_id IS NOT NULL)),
    CHECK (action NOT IN ('uploaded', 'accepted', 'correction_requested') OR file_id IS NOT NULL),
    CHECK (action NOT IN ('requested', 'cancelled') OR file_id IS NULL),
    CHECK (action NOT IN ('correction_requested', 'cancelled') OR note IS NOT NULL)
);

CREATE INDEX document_request_events_request ON document_request_events (request_id, created_at, id);

-- History, file versions and requests are never deleted, and history and versions are never edited.
CREATE FUNCTION prevent_document_record_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Document records cannot be changed or deleted';
END;
$$;
CREATE TRIGGER document_files_immutable
    BEFORE UPDATE OR DELETE ON document_files
    FOR EACH ROW EXECUTE FUNCTION prevent_document_record_change();
CREATE TRIGGER document_request_events_immutable
    BEFORE UPDATE OR DELETE ON document_request_events
    FOR EACH ROW EXECUTE FUNCTION prevent_document_record_change();
CREATE TRIGGER document_requests_no_delete
    BEFORE DELETE ON document_requests
    FOR EACH ROW EXECUTE FUNCTION prevent_document_record_change();

-- Only the status of a request may change, and only along the allowed steps.
-- Accepted and cancelled requests are final.
CREATE FUNCTION enforce_document_request_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.account_id <> OLD.account_id OR NEW.title <> OLD.title
       OR NEW.instructions IS DISTINCT FROM OLD.instructions
       OR NEW.requested_by <> OLD.requested_by OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'Only the status of a document request can change';
    END IF;
    IF (OLD.status, NEW.status) NOT IN (
        ('requested', 'submitted'), ('requested', 'cancelled'),
        ('submitted', 'accepted'), ('submitted', 'needs_correction'),
        ('submitted', 'cancelled'),
        ('needs_correction', 'submitted'), ('needs_correction', 'cancelled')
    ) THEN
        RAISE EXCEPTION 'A document request cannot change from % to %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER document_requests_status_steps
    BEFORE UPDATE ON document_requests
    FOR EACH ROW EXECUTE FUNCTION enforce_document_request_change();
