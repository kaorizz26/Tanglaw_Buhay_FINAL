"""Reconstruct private active-batch DataFrames without scoring or database writes."""

from copy import deepcopy
import re

import pandas as pd
import psycopg
from psycopg.pq import TransactionStatus

from db import get_connection
from repositories import dataset_batches as repository
from services.data_loader import EXCEL_FILES, REFERENCE_FILES


class ActiveBatchError(RuntimeError):
    """Fixed failure code that contains no source values or database details."""


def _batch_metadata(batches):
    """Require the approved pilot and complete column/file reconstruction metadata."""
    if not batches:
        raise ActiveBatchError("active_batch_missing")
    if len(batches) != 1:
        raise ActiveBatchError("invalid_active_batch_metadata")
    batch = batches[0]
    if not isinstance(batch, dict):
        raise ActiveBatchError("invalid_active_batch_metadata")
    filenames = {name: f"{name}.csv" for name in EXCEL_FILES}
    filenames.update(REFERENCE_FILES)
    columns = batch.get("source_columns")
    provenance = batch.get("source_provenance")
    if (
        type(batch.get("id")) is not int or batch["id"] <= 0
        or type(batch.get("survey_year")) is not int or batch["survey_year"] != 2022
        or batch.get("municipality") != "Pozorrubio"
        or batch.get("is_active") is not True
        or batch.get("validation_status") != "validated"
        or not isinstance(columns, dict) or set(columns) != set(filenames)
        or not isinstance(provenance, dict)
        or not isinstance(provenance.get("declared"), dict)
        or not provenance["declared"]
        or not isinstance(provenance.get("files"), dict)
        or set(provenance["files"]) != set(filenames)
    ):
        raise ActiveBatchError("invalid_active_batch_metadata")
    for name, filename in filenames.items():
        names = columns[name]
        details = provenance["files"][name]
        if (
            not isinstance(names, list) or not names
            or any(not isinstance(value, str) or not value.strip() for value in names)
            or len(set(names)) != len(names)
            or not isinstance(details, dict)
            or details.get("filename") != filename
            or type(details.get("records")) is not int or details["records"] < 0
            or not isinstance(details.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", details["sha256"])
        ):
            raise ActiveBatchError("invalid_active_batch_metadata")
    return deepcopy({
        "batch_id": batch["id"], "survey_year": batch["survey_year"],
        "municipality": batch["municipality"],
        "validation_status": batch["validation_status"], "is_active": True,
        "source_columns": columns, "source_provenance": provenance,
    })


def _reconstruct_frame(connection, metadata, name):
    """Keep JSON strings unchanged; reject missing cells, positions or rows."""
    columns = metadata["source_columns"][name]
    expected_keys = set(columns)
    records = []
    for position, responses in repository.iter_batch_records(
        connection, metadata["batch_id"], name,
    ):
        if (
            type(position) is not int or position != len(records) + 1
            or not isinstance(responses, dict) or set(responses) != expected_keys
            or any(not isinstance(value, str) for value in responses.values())
        ):
            raise ActiveBatchError("invalid_active_batch_records")
        records.append(responses)
    expected_count = metadata["source_provenance"]["files"][name]["records"]
    if len(records) != expected_count:
        raise ActiveBatchError("active_batch_record_count_mismatch")
    return pd.DataFrame(records, columns=columns, dtype=object)


def load_active_batch(connection_factory=get_connection):
    """Return (datasets, references, metadata) from one read-only database snapshot.

    The factory must supply a new, idle, caller-owned psycopg connection. The
    default uses Flask's configured DATABASE_URL. Original response strings,
    CSV row/column order and imported provenance are preserved. No engines,
    file readers, import functions or routes are invoked. Returned frames and
    metadata are internal backend objects; callers must enforce authorization.
    """
    try:
        with connection_factory() as connection:
            if connection.info.transaction_status != TransactionStatus.IDLE:
                raise ActiveBatchError("active_batch_connection_not_idle")
            with connection.transaction():
                # One stable view prevents metadata/rows from spanning activations.
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
                )
                metadata = _batch_metadata(repository.find_active_batches(connection))
                datasets = {
                    name: _reconstruct_frame(connection, metadata, name)
                    for name in EXCEL_FILES
                }
                references = {
                    name: _reconstruct_frame(connection, metadata, name)
                    for name in REFERENCE_FILES
                }
    except ActiveBatchError:
        raise
    except (psycopg.Error, RuntimeError):
        raise ActiveBatchError("active_batch_unavailable") from None
    return datasets, references, metadata
