-- Citizen Accounts: staff-created portal accounts, activation/password-reset
-- credentials and an append-only history of staff actions.
-- Apply once, explicitly, after migration 0002 (staff_users) and 0001 (households).
-- It does not depend on 0013 (Data Import) and changes no existing table.
-- Cross-year household matching is deferred: an account belongs to one household
-- record in one survey batch.

CREATE TABLE citizen_accounts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id bigint NOT NULL,
    household_uuid text NOT NULL,
    -- Entered by staff; never copied from CBMS survey answers.
    representative_name text NOT NULL
        CHECK (char_length(btrim(representative_name)) BETWEEN 1 AND 120),
    -- Stored lowercase and trimmed so the unique index cannot be bypassed by case.
    email text NOT NULL
        CHECK (email = lower(btrim(email))
               AND char_length(email) BETWEEN 6 AND 254
               AND email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    -- Empty until the citizen sets a password during activation.
    password_hash text
        CHECK (password_hash IS NULL OR password_hash LIKE 'scrypt:%$%$%'),
    status text NOT NULL DEFAULT 'pending_activation'
        CHECK (status IN ('pending_activation', 'active', 'deactivated')),
    -- Raised whenever sessions must stop working (password, email or status change).
    session_version bigint NOT NULL DEFAULT 1 CHECK (session_version > 0),
    failed_login_count integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until timestamptz,
    last_login_at timestamptz,
    -- Time of the most recent activation.
    activated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_id, household_uuid) REFERENCES households (batch_id, uuid),
    -- One permanent account per household record, deactivated accounts included.
    UNIQUE (batch_id, household_uuid),
    UNIQUE (email),
    CHECK (status <> 'active' OR (password_hash IS NOT NULL AND activated_at IS NOT NULL)),
    CHECK (status <> 'pending_activation' OR password_hash IS NULL)
);

CREATE TABLE citizen_account_tokens (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES citizen_accounts (id),
    purpose text NOT NULL CHECK (purpose IN ('activation', 'password_reset')),
    -- Keyed hashes only (HMAC-SHA256 as 64 hex characters); the link token and
    -- the six-digit code are never stored.
    token_hash text NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    code_hash text CHECK (code_hash IS NULL OR code_hash ~ '^[0-9a-f]{64}$'),
    failed_code_attempts smallint NOT NULL DEFAULT 0
        CHECK (failed_code_attempts BETWEEN 0 AND 5),
    expires_at timestamptz NOT NULL,
    -- used_at: successfully consumed. revoked_at: replaced, locked out by wrong
    -- codes, or cancelled by a deactivation or email change.
    used_at timestamptz,
    revoked_at timestamptz,
    -- Set only when the mail server accepted the message; this is not inbox delivery.
    email_accepted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Activation pairs a link with a code; password reset uses the link alone.
    CHECK ((purpose = 'activation') = (code_hash IS NOT NULL)),
    CHECK (expires_at > created_at),
    CHECK (used_at IS NULL OR revoked_at IS NULL)
);

-- At most one usable link per account and purpose, so a resend must replace the old pair.
CREATE UNIQUE INDEX citizen_account_tokens_one_open
    ON citizen_account_tokens (account_id, purpose)
    WHERE used_at IS NULL AND revoked_at IS NULL;
CREATE INDEX citizen_account_tokens_recent
    ON citizen_account_tokens (account_id, purpose, created_at);

CREATE TABLE citizen_account_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES citizen_accounts (id),
    -- The signed-in LGU Staff member who performed the action.
    staff_id bigint NOT NULL REFERENCES staff_users (id),
    action text NOT NULL
        CHECK (action IN ('created', 'activation_resent', 'email_changed',
                          'deactivated', 'reactivated')),
    note text NOT NULL CHECK (char_length(btrim(note)) BETWEEN 1 AND 500),
    representative_verified boolean NOT NULL DEFAULT false,
    privacy_consent_confirmed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Actions that grant or restore access need both staff confirmations.
    CHECK (action NOT IN ('created', 'email_changed', 'reactivated')
           OR (representative_verified AND privacy_consent_confirmed))
);

CREATE INDEX citizen_account_events_account
    ON citizen_account_events (account_id, created_at, id);

CREATE FUNCTION prevent_citizen_account_event_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Citizen account history is append-only';
END;
$$;
CREATE TRIGGER citizen_account_events_immutable
    BEFORE UPDATE OR DELETE ON citizen_account_events
    FOR EACH ROW EXECUTE FUNCTION prevent_citizen_account_event_change();
