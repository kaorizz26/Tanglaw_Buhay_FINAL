"""Permanently delete an unused inactive import while retaining its audit."""

import psycopg

from db import get_connection
from repositories import dataset_batches
from services import read_cache
from services.import_assessment_preparation import PREPARATION_LOCK_KEY
from services.import_sources import ADMIN_UPLOAD_SOURCES


class BatchDeletionError(RuntimeError):
    """Fixed deletion failure that contains no household or database values."""


def delete_import_batch(batch_id, actor_id, confirmation,
                        *, connection_factory=get_connection):
    """Delete one eligible batch and every batch-scoped source and assessment row."""
    if (type(batch_id) is not int or batch_id <= 0
            or type(actor_id) is not int or actor_id <= 0
            or confirmation != f"DELETE BATCH {batch_id}"):
        raise BatchDeletionError("invalid_deletion_request")
    try:
        with connection_factory() as connection:
            with connection.transaction():
                if not connection.execute(
                        "SELECT pg_try_advisory_xact_lock(%s)",
                        (PREPARATION_LOCK_KEY,),
                ).fetchone()[0]:
                    raise BatchDeletionError("batch_busy")
                dataset_batches.lock_dataset_imports(connection)
                actor = connection.execute(
                    "SELECT role, is_active FROM staff_users WHERE id = %s FOR SHARE",
                    (actor_id,),
                ).fetchone()
                if actor != ("system_administrator", True):
                    raise BatchDeletionError("unauthorized")
                batch = dataset_batches.lock_import_batch_for_deletion(
                    connection, batch_id,
                )
                if batch is None:
                    raise BatchDeletionError("batch_missing")
                if batch["is_active"]:
                    raise BatchDeletionError("active_batch")
                declared = batch["source_provenance"].get("declared", {})
                if (batch["validation_status"] != "validated"
                        or declared.get("source") not in ADMIN_UPLOAD_SOURCES):
                    raise BatchDeletionError("batch_not_deletable")
                blockers = dataset_batches.import_batch_deletion_blockers(
                    connection, batch_id,
                )
                if blockers["audit_rows"] != 1:
                    raise BatchDeletionError("audit_missing")
                if blockers["reference_uses"]:
                    raise BatchDeletionError("batch_referenced")
                if any(blockers[name] for name in (
                        "activation_events", "staff_reviews", "citizen_accounts")):
                    raise BatchDeletionError("retained_history")
                removed = dataset_batches.delete_import_batch_records(
                    connection, batch_id, actor_id,
                )
                result = {
                    "batch_id": batch_id,
                    "survey_year": batch["survey_year"],
                    **removed,
                }
        read_cache.clear()
        return result
    except BatchDeletionError:
        raise
    except (psycopg.Error, RuntimeError, TypeError, KeyError):
        raise BatchDeletionError("deletion_unavailable") from None
