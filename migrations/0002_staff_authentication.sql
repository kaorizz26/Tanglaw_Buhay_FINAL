-- Staff identities are separate from imported survey records.
CREATE TABLE staff_users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username text NOT NULL UNIQUE
        CHECK (username ~ '^[a-z0-9][a-z0-9_.-]{2,63}$'),
    password_hash text NOT NULL CHECK (password_hash LIKE 'scrypt:%$%$%'),
    role text NOT NULL CHECK (role IN ('system_administrator', 'lgu_staff')),
    is_active boolean NOT NULL DEFAULT true,
    session_version bigint NOT NULL DEFAULT 1 CHECK (session_version > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
