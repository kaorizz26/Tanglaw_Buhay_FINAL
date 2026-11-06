-- Permit approved later-year imports while retaining the 2022 lower boundary.
-- The web service separately rejects years after the current Philippine year.
ALTER TABLE dataset_batches
    DROP CONSTRAINT dataset_batches_survey_year_check;

ALTER TABLE dataset_batches
    ADD CONSTRAINT dataset_batches_survey_year_check
    CHECK (survey_year >= 2022);

ALTER TABLE import_validation_events
    ADD COLUMN survey_year integer;

UPDATE import_validation_events event
SET survey_year = batch.survey_year
FROM dataset_batches batch
WHERE event.batch_id = batch.id;

ALTER TABLE import_validation_events
    ADD CONSTRAINT import_validation_events_survey_year_check
    CHECK (survey_year IS NULL OR survey_year >= 2022),
    ADD CONSTRAINT import_validation_events_staged_year_check
    CHECK (outcome <> 'staged' OR survey_year IS NOT NULL);
