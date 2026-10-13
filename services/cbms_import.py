"""Internal transactional CSV importer; no route, upload UI or authentication."""

import json

import psycopg

from db import get_connection
from repositories import dataset_batches as repository
from services.cbms_preflight import load_csv_bundle


def import_csv_bundle(
    data_dir, *, survey_year, municipality, source_provenance,
    connection_factory=get_connection,
) -> dict:
    """Validate one CSV snapshot, then atomically store and activate its batch.

    The connection factory must return a new, caller-owned psycopg connection;
    the default uses the existing Flask DATABASE_URL configuration. Metadata is
    explicitly declared by the caller, not inferred from historical responses.
    Results contain only technical batch IDs, aggregate validation and fixed
    errors. This backend function must remain unexposed until admin permissions
    are implemented. Failed imports leave no new batch or source rows behind.
    """
    result = {"imported": False, "batch_id": None, "validation": None, "error": None}
    if not _valid_metadata(survey_year, municipality, source_provenance):
        result["error"] = {"code": "invalid_import_metadata"}
        return result
    datasets, references, report = load_csv_bundle(data_dir)
    result["validation"] = report
    if not report["passed"]:
        result["error"] = {"code": "preflight_failed"}
        return result
    frames = {**datasets, **references}
    columns = {name: list(frame.columns) for name, frame in frames.items()}
    provenance = {
        "declared": source_provenance,
        "files": {
            name: {**frame.attrs, "records": len(frame)} for name, frame in frames.items()
        },
    }
    try:
        with connection_factory() as connection:
            with connection.transaction():
                repository.lock_dataset_imports(connection)
                batch_id = repository.create_batch(
                    connection, survey_year, municipality, columns, provenance,
                )
                repository.store_batch_records(connection, batch_id, datasets, references)
                repository.verify_batch_counts(connection, batch_id, datasets, references)
                repository.activate_batch(connection, batch_id)
    except (psycopg.Error, RuntimeError):
        # SQL/connection exception details may contain identifiers or credentials.
        result["error"] = {"code": "database_import_failed"}
        return result
    result.update({"imported": True, "batch_id": batch_id})
    return result


def _valid_metadata(survey_year, municipality, provenance):
    """Require the approved pilot and nonempty JSON-compatible declared provenance."""
    if type(survey_year) is not int or survey_year != 2022 or municipality != "Pozorrubio":
        return False
    if not isinstance(provenance, dict) or not provenance:
        return False
    try:
        json.dumps(provenance, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return True
