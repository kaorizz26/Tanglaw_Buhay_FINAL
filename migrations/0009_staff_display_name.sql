-- Store a person's full name separately from their unique login username.
-- Existing accounts remain valid until their display names are provided.
ALTER TABLE staff_users
    ADD COLUMN display_name text
        CHECK (display_name IS NULL OR
               (char_length(btrim(display_name)) BETWEEN 1 AND 120));
