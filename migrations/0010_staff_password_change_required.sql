-- Existing accounts keep their current login flow. Account creation will set
-- this flag for new staff until they choose their own password.
ALTER TABLE staff_users
    ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;
