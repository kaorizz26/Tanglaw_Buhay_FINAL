"""Admin-only four-file upload preflight; no source or assessment writes."""

from hashlib import sha256
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, gettempdir
from time import time

from flask import current_app
import psycopg

from db import get_connection
from repositories import import_validation_events
from services.cbms_import import DuplicateImportBundle, stage_validated_snapshot
from services.cbms_preflight import load_csv_bundle
from services.data_loader import EXCEL_FILES
from services.import_references import load_import_references


UPLOAD_BYTES = 256 * 1024 * 1024
STALE_SECONDS = 24 * 60 * 60
STAGING_PREFIX = "tanglaw-import-"
STAGING_MARKER = ".tanglaw-import"
IMPORT_LOCK_KEY = 20222026


class ImportValidationUnavailable(RuntimeError):
    """The validation service cannot safely complete this attempt."""


class ImportUploadError(ValueError):
    """A fixed user-facing upload error without untrusted filenames."""


def _staging_root():
    """Keep temporary source files outside the application and web directory."""
    configured = current_app.config.get("IMPORT_STAGING_DIR") or gettempdir()
    root = Path(configured).resolve()
    application = Path(current_app.root_path).resolve()
    if root == application or application in root.parents:
        raise ImportValidationUnavailable("private_staging_unavailable")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_staged_directory(child, root):
    """Restrict cleanup and quota checks to directories created by this feature."""
    return (child.name.startswith(STAGING_PREFIX) and not child.is_symlink()
            and child.is_dir() and child.resolve().parent == root
            and (child / STAGING_MARKER).is_file()
            and not (child / STAGING_MARKER).is_symlink())


def _cleanup_stale(root):
    """Remove abandoned staging folders after one day, under the import lock."""
    cutoff = time() - STALE_SECONDS
    for child in root.iterdir():
        if not _is_staged_directory(child, root):
            continue
        if child.stat().st_mtime < cutoff:
            shutil.rmtree(child)


def _staged_bytes(root):
    """Count only this feature's temporary files before accepting another bundle."""
    total = 0
    for child in root.iterdir():
        if not _is_staged_directory(child, root):
            continue
        for path in child.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


def _check_files(files):
    """Accept one upload for each exact source name and no extra file fields."""
    if set(files) != set(EXCEL_FILES):
        raise ImportUploadError("Choose all four required CSV files only.")
    for name in EXCEL_FILES:
        chosen = files.getlist(name)
        if len(chosen) != 1 or chosen[0].filename != f"{name}.csv":
            raise ImportUploadError("Use the required CSV filenames shown on this page.")


def _save_files(files, root):
    """Copy bounded streams to canonical private names and hash the same bytes."""
    metadata = {}
    total = 0
    raw = root / "raw"
    raw.mkdir()
    for name in EXCEL_FILES:
        digest = sha256()
        size = 0
        with (raw / f"{name}.csv").open("xb") as target:
            while chunk := files[name].stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if total > UPLOAD_BYTES:
                    raise ImportUploadError("The four files exceed the 256 MiB upload limit.")
                target.write(chunk)
                digest.update(chunk)
        metadata[name] = {"bytes": size, "sha256": digest.hexdigest()}
    return metadata


def _snapshot_provenance(datasets, references, metadata, actor_id,
                         reference_batch_id, reference_version):
    """Bind stored rows to the exact source bytes and approved reference version."""
    file_details = {}
    for name, frame in {**datasets, **references}.items():
        filename = frame.attrs.get("filename")
        digest = frame.attrs.get("sha256")
        if not filename or not digest:
            raise ImportValidationUnavailable("import_snapshot_invalid")
        if name in metadata and metadata[name]["sha256"] != digest:
            raise ImportValidationUnavailable("import_snapshot_invalid")
        file_details[name] = {
            "filename": filename, "sha256": digest, "records": len(frame),
        }
    source_identity = "|".join(
        f"{name}:{metadata[name]['sha256']}" for name in EXCEL_FILES
    ) + f"|references:{reference_version}"
    bundle_sha256 = sha256(source_identity.encode("ascii")).hexdigest()
    return {
        "declared": {
            "source": "administrator_uploaded_2022_compatible_csv",
            "authorized_by_staff_id": actor_id,
            "reference_batch_id": reference_batch_id,
            "reference_version": reference_version,
            "bundle_sha256": bundle_sha256,
        },
        "files": file_details,
    }


def validate_admin_upload(files, actor_id, *, store_inactive=False,
                          authorization_confirmed=False,
                          connection_factory=get_connection):
    """Preflight four CSVs; optionally store a passing snapshot as inactive."""
    result = {"report": None, "error": None, "batch_id": None}
    metadata = {}
    reference_batch_id = None
    reference_version = None
    outcome = "rejected"
    try:
        with connection_factory() as connection:
            locked = connection.execute(
                "SELECT pg_try_advisory_lock(%s)", (IMPORT_LOCK_KEY,),
            ).fetchone()[0]
            connection.commit()
            try:
                if not locked:
                    raise ImportUploadError(
                        "Another data validation is in progress. Try again later.",
                    )
                _check_files(files)
                if store_inactive and not authorization_confirmed:
                    raise ImportUploadError(
                        "Confirm that these files are authorized 2022 Pozorrubio CBMS data.",
                    )
                root = _staging_root()
                _cleanup_stale(root)
                if (_staged_bytes(root) + UPLOAD_BYTES
                        > current_app.config["IMPORT_STAGING_BYTES"]):
                    raise ImportUploadError("Temporary upload storage is full. Try again later.")
                with TemporaryDirectory(prefix=STAGING_PREFIX, dir=root) as directory:
                    (Path(directory) / STAGING_MARKER).write_text("validation", encoding="ascii")
                    metadata = _save_files(files, Path(directory))
                    reference_batch_id = current_app.config["IMPORT_REFERENCE_BATCH_ID"]
                    with connection.transaction():
                        connection.execute(
                            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
                        )
                        references, reference_version = load_import_references(
                            connection, reference_batch_id,
                        )
                    datasets, references, result["report"] = load_csv_bundle(
                        directory, reference_frames=references,
                    )
                outcome = "passed" if result["report"]["passed"] else "failed"
                if outcome == "passed" and store_inactive:
                    provenance = _snapshot_provenance(
                        datasets, references, metadata, actor_id,
                        reference_batch_id, reference_version,
                    )
                    try:
                        with connection.transaction():
                            batch_id = stage_validated_snapshot(
                                connection, datasets, references, provenance,
                            )
                            import_validation_events.record_attempt(
                                connection, actor_id, "staged", reference_batch_id,
                                reference_version, metadata, 0,
                                len(result["report"]["warnings"]), batch_id,
                            )
                            import_validation_events.purge_expired_attempts(connection)
                    except DuplicateImportBundle:
                        result["error"] = "This exact bundle is already stored."
                        outcome = "rejected"
                    except (psycopg.Error, RuntimeError):
                        # The storage transaction has rolled back. Audit the attempt
                        # separately when the connection is still usable.
                        try:
                            with connection.transaction():
                                import_validation_events.record_attempt(
                                    connection, actor_id, "rejected",
                                    reference_batch_id, reference_version,
                                    metadata, 1,
                                    len(result["report"]["warnings"]),
                                )
                                import_validation_events.purge_expired_attempts(connection)
                        except (psycopg.Error, RuntimeError):
                            pass
                        raise ImportValidationUnavailable(
                            "import_storage_unavailable",
                        ) from None
                    else:
                        result["batch_id"] = batch_id
                        return result
            except ImportUploadError as exc:
                result["error"] = str(exc)
            errors = len(result["report"]["errors"]) if result["report"] else 1
            warnings = len(result["report"]["warnings"]) if result["report"] else 0
            with connection.transaction():
                import_validation_events.record_attempt(
                    connection, actor_id, outcome, reference_batch_id,
                    reference_version, metadata, errors, warnings,
                )
                import_validation_events.purge_expired_attempts(connection)
    except (psycopg.Error, OSError, RuntimeError, ValueError, KeyError):
        raise ImportValidationUnavailable("import_validation_unavailable") from None
    return result


def record_oversized_attempt(actor_id, *, connection_factory=get_connection):
    """Audit a request rejected before the uploaded streams can be inspected."""
    try:
        with connection_factory() as connection, connection.transaction():
            import_validation_events.record_attempt(
                connection, actor_id, "rejected", None, None, {}, 1, 0,
            )
            import_validation_events.purge_expired_attempts(connection)
    except (psycopg.Error, RuntimeError):
        raise ImportValidationUnavailable("import_validation_unavailable") from None
