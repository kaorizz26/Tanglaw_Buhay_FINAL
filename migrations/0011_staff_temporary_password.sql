-- Apply once after 0009 and 0010. Existing hashes cannot recover old passwords.
-- Only the initial password is encrypted; staff-chosen passwords stay hash-only.
ALTER TABLE staff_users
    ADD COLUMN temporary_password_encrypted text,
    ADD CONSTRAINT staff_temporary_password_pending
        CHECK (temporary_password_encrypted IS NULL OR
               (role = 'lgu_staff' AND must_change_password));
