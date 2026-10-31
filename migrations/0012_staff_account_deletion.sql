-- Keep staff identities referenced by reviews and recommendation history.
ALTER TABLE staff_users
    ADD COLUMN deleted_at timestamptz;
