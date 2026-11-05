"""One-time sync of supplemental household contact numbers into the active batch.

Dry-run by default. Writing requires --execute, a typed confirmation phrase and
an existing backup file. The sync only adds the `contact_number` key to
`households.responses` and records the new column and file hash in the batch
metadata, all in one transaction. It never logs contact numbers.
"""

import argparse
from copy import deepcopy
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from repositories import dataset_batches as repository

FIELD = "contact_number"
CONTACT_PATTERN = re.compile(r"09[0-9]{9}")
EXPECTED_HOUSEHOLDS = 19304
DEVELOPMENT_DATABASE = "tanglaw_buhay_dev"
CONFIRMATION = "WRITE-CONTACT-NUMBERS-TO-tanglaw_buhay_dev"
DEFAULT_CSV = ROOT / "data" / "raw" / "household.csv"
SUPPLEMENTAL_NOTE = {
    "origin": "Supplemental synthetic field added after the original import.",
    "source_file": "household.csv",
    "assumption": "Not an original PSA survey answer and not verified by PSA.",
}
# Tables that this sync must leave exactly as they were.
PROTECTED_TABLES = (
    "household_members", "member_health", "child_death_records", "codebook_values",
    "mpi_code_mappings", "variable_definitions", "assessment_runs",
    "household_assessments", "assessment_run_selections", "staff_reviews",
    "staff_review_notes", "staff_review_reassignments",
    "staff_review_recommendation_changes", "staff_users",
)


class SyncError(RuntimeError):
    """Fixed failure code; messages never contain source values."""


def require(condition, code):
    """Stop with a fixed code when a safety condition fails."""
    if not condition:
        raise SyncError(code)


def read_source_csv(path):
    """Return (columns, rows, sha256) with every cell kept as text."""
    try:
        content = Path(path).read_bytes()
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader, [])
        rows = list(reader)
    except FileNotFoundError:
        raise SyncError("csv_missing") from None
    except (OSError, UnicodeError, csv.Error):
        raise SyncError("csv_unreadable") from None
    require(columns and all(name.strip() for name in columns), "csv_blank_header")
    require(len(set(columns)) == len(columns), "csv_duplicate_header")
    require("uuid" in columns and FIELD in columns, "csv_missing_required_column")
    require(all(len(row) == len(columns) for row in rows), "csv_inconsistent_row_width")
    records = [dict(zip(columns, row)) for row in rows]
    return columns, records, sha256(content).hexdigest()


def validate_source(records, expected_households):
    """Require the expected household count, unique UUIDs and valid mobile numbers."""
    require(len(records) == expected_households, "csv_household_count_mismatch")
    uuids = [record["uuid"] for record in records]
    require(all(value.strip() for value in uuids), "csv_blank_uuid")
    require(len(set(uuids)) == len(uuids), "csv_duplicate_uuid")
    require(
        all(CONTACT_PATTERN.fullmatch(record[FIELD]) for record in records),
        "csv_invalid_contact_number",
    )


def table_fingerprint(connection, table):
    """Return an order-independent (row count, row hash) for one table, or None."""
    exists = connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]
    if exists is None:
        return None
    return connection.execute(
        f"SELECT count(*), coalesce(sum(hashtextextended(t::text, 0)::numeric), 0) "
        f"FROM {table} t",
    ).fetchone()


def protected_fingerprints(connection):
    """Fingerprint every table that must not change."""
    return {table: table_fingerprint(connection, table) for table in PROTECTED_TABLES}


def read_target_batch(connection):
    """Return the single active validated 2022 Pozorrubio batch, else stop."""
    batches = repository.find_active_batches(connection)
    require(len(batches) == 1, "active_batch_not_unique")
    batch = batches[0]
    require(
        batch["survey_year"] == 2022 and batch["municipality"] == "Pozorrubio"
        and batch["validation_status"] == "validated" and batch["is_active"] is True,
        "unexpected_active_batch",
    )
    return batch


def plan_metadata(batch, columns, new_hash, record_count):
    """Return the new (source_columns, source_provenance) or stop if unexpected."""
    old_columns = batch["source_columns"].get("household")
    require(isinstance(old_columns, list), "household_columns_missing")
    require(FIELD not in old_columns, "contact_number_already_in_metadata")
    position = columns.index(FIELD)
    new_columns = old_columns[:position] + [FIELD] + old_columns[position:]
    require(new_columns == columns, "csv_columns_differ_from_batch")
    provenance = deepcopy(batch["source_provenance"])
    details = provenance.get("files", {}).get("household")
    require(isinstance(details, dict), "household_provenance_missing")
    require(details.get("records") == record_count, "provenance_record_count_mismatch")
    require(details.get("sha256") != new_hash, "csv_hash_already_recorded")
    details["sha256"] = new_hash
    supplemental = provenance.setdefault("supplemental_fields", {})
    require(FIELD not in supplemental.get("household", {}), "supplemental_note_exists")
    supplemental.setdefault("household", {})[FIELD] = SUPPLEMENTAL_NOTE
    return {**batch["source_columns"], "household": new_columns}, provenance


def check_households_match(connection, batch_id, columns, records):
    """Require identical UUIDs, row positions and existing values, without contact_number."""
    existing_keys = set(columns) - {FIELD}
    seen = 0
    for position, responses in repository.iter_batch_records(connection, batch_id, "household"):
        require(position == seen + 1 and seen < len(records), "household_rows_differ")
        source = records[seen]
        require(
            FIELD not in responses and set(responses) == existing_keys
            and all(responses[key] == source[key] for key in existing_keys),
            "database_differs_from_csv",
        )
        seen += 1
    require(seen == len(records), "household_rows_differ")


def write_contact_numbers(connection, batch_id, records):
    """Add only the contact_number key to each household in the batch."""
    with connection.cursor() as cursor:
        cursor.executemany(
            """UPDATE households
               SET responses = responses || jsonb_build_object(%s::text, %s::text)
               WHERE batch_id = %s AND uuid = %s AND source_row_number = %s
                 AND NOT (responses ? %s)""",
            [(FIELD, record[FIELD], batch_id, record["uuid"], position, FIELD)
             for position, record in enumerate(records, 1)],
        )
        require(cursor.rowcount == len(records), "unexpected_update_count")


def write_metadata(connection, batch_id, new_columns, new_provenance):
    """Store the new column list and provenance on the target batch only."""
    changed = connection.execute(
        """UPDATE dataset_batches SET source_columns = %s, source_provenance = %s
           WHERE id = %s AND is_active AND validation_status = 'validated'""",
        (Jsonb(new_columns), Jsonb(new_provenance), batch_id),
    ).rowcount
    require(changed == 1, "unexpected_metadata_update_count")


def verify_written(connection, batch, columns, records, new_columns, new_provenance, before):
    """Re-read everything inside the transaction; any mismatch rolls it back."""
    expected_keys = set(columns)
    seen = 0
    for position, responses in repository.iter_batch_records(connection, batch["id"], "household"):
        require(position == seen + 1 and seen < len(records), "verify_row_positions")
        source = records[seen]
        require(
            set(responses) == expected_keys
            and all(responses[key] == source[key] for key in expected_keys),
            "verify_household_values",
        )
        seen += 1
    require(seen == len(records), "verify_household_count")
    (after,) = repository.find_active_batches(connection)
    require(
        after["id"] == batch["id"] and after["source_columns"] == new_columns
        and after["source_provenance"] == new_provenance
        and after["survey_year"] == batch["survey_year"]
        and after["municipality"] == batch["municipality"],
        "verify_batch_metadata",
    )
    require(protected_fingerprints(connection) == before, "verify_protected_tables_changed")


def sync_contact_numbers(connection, csv_path, *, expected_households=EXPECTED_HOUSEHOLDS,
                         write=False):
    """Check CSV and database, then optionally write; returns aggregate results only.

    The connection must be new and idle. Every check and write shares one
    transaction, so any failure leaves the database unchanged.
    """
    columns, records, new_hash = read_source_csv(csv_path)
    validate_source(records, expected_households)
    require(connection.info.transaction_status == TransactionStatus.IDLE, "connection_not_idle")
    with connection.transaction():
        if write:
            repository.lock_dataset_imports(connection)
            connection.execute("LOCK TABLE households IN EXCLUSIVE MODE")
        else:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        batch = read_target_batch(connection)
        new_columns, new_provenance = plan_metadata(batch, columns, new_hash, len(records))
        check_households_match(connection, batch["id"], columns, records)
        report = {
            "mode": "write" if write else "dry-run", "batch_id": batch["id"],
            "households_checked": len(records), "households_updated": 0,
            "csv_sha256": new_hash, "committed": False,
        }
        if write:
            before = protected_fingerprints(connection)
            write_contact_numbers(connection, batch["id"], records)
            write_metadata(connection, batch["id"], new_columns, new_provenance)
            verify_written(
                connection, batch, columns, records, new_columns, new_provenance, before,
            )
            report.update(households_updated=len(records), committed=True)
    return report


def connect_to_development(read_only):
    """Open a loopback-only connection pinned to the local development database."""
    from config import Config

    settings = conninfo_to_dict(Config.DATABASE_URL or "")
    require(settings.get("dbname") == DEVELOPMENT_DATABASE, "wrong_database_configuration")
    require(settings.get("host") in ("localhost", "127.0.0.1", "::1"), "nonlocal_configuration")
    require(
        not any(os.getenv(name) for name in ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")),
        "service_or_options_environment",
    )
    options = "-c search_path=public -c lock_timeout=10000 -c statement_timeout=300000"
    settings.update(
        hostaddr="::1" if settings["host"] == "::1" else "127.0.0.1",
        connect_timeout=10, application_name="tanglaw_contact_number_sync",
        options=options + (" -c default_transaction_read_only=on" if read_only else ""),
    )
    connection = psycopg.connect(**settings)
    require(connection.info.dbname == DEVELOPMENT_DATABASE, "connected_database_mismatch")
    return connection


def parse_arguments(argv):
    """Read command-line options; the default mode is a dry run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--execute", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument("--confirm", default="", help="exact confirmation phrase for --execute")
    parser.add_argument("--backup-file", type=Path, help="existing pg_dump file, required to write")
    return parser.parse_args(argv)


def main(argv=None):
    """Run the dry-run or confirmed write and print a JSON aggregate result."""
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        if arguments.execute:
            require(arguments.confirm == CONFIRMATION, "confirmation_phrase_required")
            backup = arguments.backup_file
            require(backup is not None and backup.is_file() and backup.stat().st_size > 0,
                    "backup_file_required")
        with connect_to_development(read_only=not arguments.execute) as connection:
            report = sync_contact_numbers(connection, arguments.csv, write=arguments.execute)
    except SyncError as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    except (psycopg.Error, OSError):
        # Database errors may include identifiers or credentials.
        print(json.dumps({"ok": False, "error": "database_or_file_error"}))
        return 1
    print(json.dumps({"ok": True, **report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
