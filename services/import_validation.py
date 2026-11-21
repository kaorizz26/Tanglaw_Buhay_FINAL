"""Admin-only four-file upload preflight; no source or assessment writes."""

from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, gettempdir
from time import time

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import psycopg

from db import get_connection
from repositories import dataset_batches, import_validation_events
from services.cbms_import import (
    DuplicateImportBundle, DuplicateSurveyYear, stage_validated_snapshot,
)
from services.cbms_preflight import load_csv_bundle
from services.data_loader import EXCEL_FILES
from services.import_sources import ADMIN_UPLOAD_SOURCE
from services.import_references import (
    ImportReferenceError, load_import_references, load_system_import_references,
)
from services.survey_years import is_supported_survey_year, latest_survey_year


FILE_UPLOAD_MB = 64
FILE_UPLOAD_BYTES = FILE_UPLOAD_MB * 1024 * 1024
UPLOAD_BYTES = 256 * 1024 * 1024
STALE_SECONDS = 24 * 60 * 60
STAGING_PREFIX = "tanglaw-import-"
STAGING_MARKER = ".tanglaw-import"
IMPORT_LOCK_KEY = 20222026
PREVIEW_TOKEN_MAX_AGE = 30 * 60
PREVIEW_TOKEN_SALT = "admin-import-preview-v1"
UPLOAD_LABELS = {
    "household": "Households",
    "section_a_e_indiv": "Household members",
    "section_l02_to_l41": "Member health records",
    "section_l11_to_l14": "Child death records",
}
AUDIT_DESCRIPTIONS = {
    "upload_rejected": "Rejected the upload before file validation completed.",
    "validation_passed": "Validated the selected files without storing a dataset.",
    "validation_failed": "Validation found blocking file or data issues; nothing was stored.",
    "staged": "Stored the validated files as a new inactive dataset batch.",
    "same_year_reupload": (
        "Stored a separate inactive batch for a survey year that already had data; "
        "no existing batch was overwritten."
    ),
    "duplicate_bundle": "Blocked an exact duplicate of source files already stored.",
    "duplicate_survey_year": (
        "Blocked storage because the survey year already had data and re-upload "
        "confirmation was missing."
    ),
    "storage_unavailable": "Storage failed after validation; no dataset was changed.",
    "request_too_large": "Rejected the upload because the combined request was too large.",
}


class ImportValidationUnavailable(RuntimeError):
    """The validation service cannot safely complete this attempt."""


class ImportUploadError(ValueError):
    """A fixed user-facing upload error without untrusted filenames."""


class ImportPreviewError(ValueError):
    """The submitted preview confirmation is missing, expired or invalid."""


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
        if len(chosen) != 1:
            raise ImportUploadError(
                f"Choose one {UPLOAD_LABELS[name]} file named {name}.csv."
            )
        filename = chosen[0].filename or ""
        if not filename.lower().endswith(".csv"):
            raise ImportUploadError(
                f"{UPLOAD_LABELS[name]} must be a CSV file named {name}.csv."
            )
        if filename != f"{name}.csv":
            raise ImportUploadError(
                f"Use the exact filename {name}.csv for {UPLOAD_LABELS[name]}."
            )


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
                if size > FILE_UPLOAD_BYTES:
                    raise ImportUploadError(
                        f"{name}.csv exceeds the {FILE_UPLOAD_MB} MB file limit. "
                        "Choose a smaller file."
                    )
                if total > UPLOAD_BYTES:
                    raise ImportUploadError(
                        "The four files exceed the upload limit of about 268 MB."
                    )
                target.write(chunk)
                digest.update(chunk)
        metadata[name] = {"bytes": size, "sha256": digest.hexdigest()}
    return metadata


def _preview_digest(metadata, survey_year):
    """Identify the selected source bytes and survey year without exposing data."""
    identity = "|".join(
        f"{name}:{metadata[name]['sha256']}" for name in EXCEL_FILES
    ) + f"|survey_year:{survey_year}"
    return sha256(identity.encode("ascii")).hexdigest()


def _audit_metadata(metadata, report=None, result_code=None,
                    same_year_reupload=False):
    """Add aggregate record totals and a fixed result code to safe audit metadata."""
    audited = {
        name: dict(metadata[name]) for name in EXCEL_FILES if name in metadata
    }
    if report:
        for name in EXCEL_FILES:
            records = report.get("datasets", {}).get(name, {}).get("records")
            if name in audited and type(records) is int and records >= 0:
                audited[name]["records"] = records
    if result_code:
        description_code = (
            "same_year_reupload"
            if result_code == "staged" and same_year_reupload else result_code
        )
        audited["_result"] = {
            "code": result_code,
            "description": AUDIT_DESCRIPTIONS[description_code],
            "same_year_reupload": bool(same_year_reupload),
        }
    return audited


def create_import_preview_token(actor_id, survey_year, preview_digest):
    """Sign a passing preview for this administrator and exact file bundle."""
    serializer = URLSafeTimedSerializer(
        current_app.secret_key, salt=PREVIEW_TOKEN_SALT,
    )
    return serializer.dumps({
        "actor_id": actor_id,
        "survey_year": survey_year,
        "preview_digest": preview_digest,
    })


def verify_import_preview_token(token, actor_id, survey_year):
    """Return the expected file digest from a recent matching preview."""
    if not token or len(token) > 512:
        raise ImportPreviewError(
            "Preview and validate the selected files before confirming storage."
        )
    serializer = URLSafeTimedSerializer(
        current_app.secret_key, salt=PREVIEW_TOKEN_SALT,
    )
    try:
        preview = serializer.loads(token, max_age=PREVIEW_TOKEN_MAX_AGE)
    except SignatureExpired:
        raise ImportPreviewError(
            "This file preview has expired. Preview the selected files again."
        ) from None
    except BadSignature:
        raise ImportPreviewError(
            "This file preview is invalid. Preview the selected files again."
        ) from None
    if (preview.get("actor_id") != actor_id
            or preview.get("survey_year") != survey_year
            or not isinstance(preview.get("preview_digest"), str)):
        raise ImportPreviewError(
            "This file preview does not match the current import. Preview the files again."
        )
    return preview["preview_digest"]


def _snapshot_provenance(datasets, references, metadata, actor_id,
                         reference_batch_id, reference_version, survey_year):
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
    ) + f"|references:{reference_version}|survey_year:{survey_year}"
    bundle_sha256 = sha256(source_identity.encode("ascii")).hexdigest()
    return {
        "declared": {
            "source": ADMIN_UPLOAD_SOURCE,
            "survey_year": survey_year,
            "authorized_by_staff_id": actor_id,
            "reference_batch_id": reference_batch_id,
            "reference_version": reference_version,
            "bundle_sha256": bundle_sha256,
        },
        "files": file_details,
    }


def _approved_references(connection):
    """Use the installed reference package, with the original batch fallback."""
    reference_dir = current_app.config.get("IMPORT_REFERENCE_DIR")
    if reference_dir:
        references, version = load_system_import_references(reference_dir)
        return references, version, None
    reference_batch_id = current_app.config.get("IMPORT_REFERENCE_BATCH_ID")
    if type(reference_batch_id) is not int or reference_batch_id <= 0:
        raise ImportValidationUnavailable("system_reference_package_unavailable")
    references, version = load_import_references(connection, reference_batch_id)
    return references, version, reference_batch_id


def validate_admin_upload(files, actor_id, survey_year, *, store_inactive=False,
                          authorization_confirmed=False,
                          same_year_confirmed=False,
                          expected_preview_digest=None,
                          connection_factory=get_connection):
    """Preflight four CSVs; optionally store a passing snapshot as inactive."""
    result = {"report": None, "error": None, "batch_id": None,
              "preview_digest": None,
              "survey_year": survey_year if is_supported_survey_year(survey_year) else None}
    metadata = {}
    reference_batch_id = None
    reference_version = None
    outcome = "rejected"
    audit_code = "upload_rejected"
    audit_year = result["survey_year"]
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
                if audit_year is None:
                    raise ImportUploadError(
                        "Choose a supported survey year: every two years from "
                        f"2022 through {latest_survey_year()}.",
                    )
                _check_files(files)
                if store_inactive and not authorization_confirmed:
                    raise ImportUploadError(
                        "Confirm that these are authorized Pozorrubio CBMS files "
                        "for the survey year entered.",
                    )
                root = _staging_root()
                _cleanup_stale(root)
                if (_staged_bytes(root) + UPLOAD_BYTES
                        > current_app.config["IMPORT_STAGING_BYTES"]):
                    raise ImportUploadError("Temporary upload storage is full. Try again later.")
                with TemporaryDirectory(prefix=STAGING_PREFIX, dir=root) as directory:
                    (Path(directory) / STAGING_MARKER).write_text("validation", encoding="ascii")
                    metadata = _save_files(files, Path(directory))
                    result["preview_digest"] = _preview_digest(metadata, survey_year)
                    if (expected_preview_digest is not None
                            and not compare_digest(
                                result["preview_digest"], expected_preview_digest,
                            )):
                        raise ImportUploadError(
                            "The selected files changed after preview. Preview the files again."
                        )
                    with connection.transaction():
                        connection.execute(
                            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
                        )
                        references, reference_version, reference_batch_id = (
                            _approved_references(connection)
                        )
                    datasets, references, result["report"] = load_csv_bundle(
                        directory, reference_frames=references,
                    )
                outcome = "passed" if result["report"]["passed"] else "failed"
                audit_code = "validation_passed" if outcome == "passed" else "validation_failed"
                if outcome == "passed" and store_inactive:
                    provenance = _snapshot_provenance(
                        datasets, references, metadata, actor_id,
                        reference_batch_id, reference_version, survey_year,
                    )
                    same_year_reupload = False
                    try:
                        with connection.transaction():
                            # Derive the audit flag from stored state, not a
                            # client-controlled confirmation field. The lock
                            # keeps that state stable through staging.
                            dataset_batches.lock_dataset_imports(connection)
                            same_year_reupload = bool(
                                dataset_batches.count_validated_batches_for_year(
                                    connection, survey_year,
                                )
                            )
                            batch_id = stage_validated_snapshot(
                                connection, datasets, references, provenance, survey_year,
                                same_year_confirmed=same_year_confirmed,
                            )
                            import_validation_events.record_attempt(
                                connection, actor_id, "staged", reference_batch_id,
                                reference_version,
                                _audit_metadata(
                                    metadata, result["report"], "staged",
                                    same_year_reupload=same_year_reupload,
                                ), 0,
                                len(result["report"]["warnings"]), batch_id,
                                survey_year=survey_year,
                            )
                            import_validation_events.purge_expired_attempts(connection)
                    except DuplicateImportBundle:
                        result["error"] = (
                            "These exact CBMS source files are already stored. "
                            "Duplicate datasets cannot be imported."
                        )
                        outcome = "rejected"
                        audit_code = "duplicate_bundle"
                    except DuplicateSurveyYear:
                        result["error"] = (
                            f"Survey year {survey_year} already has a stored dataset. "
                            "Review the duplicate-year warning and confirm the re-upload "
                            "before storing a different dataset for this year."
                        )
                        outcome = "rejected"
                        audit_code = "duplicate_survey_year"
                    except (psycopg.Error, RuntimeError):
                        # The storage transaction has rolled back. Audit the attempt
                        # separately when the connection is still usable.
                        try:
                            with connection.transaction():
                                import_validation_events.record_attempt(
                                    connection, actor_id, "rejected",
                                    reference_batch_id, reference_version,
                                    _audit_metadata(
                                        metadata, result["report"], "storage_unavailable",
                                        same_year_reupload=same_year_reupload,
                                    ), 1,
                                    len(result["report"]["warnings"]),
                                    survey_year=audit_year,
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
                    reference_version,
                    _audit_metadata(metadata, result["report"], audit_code),
                    errors, warnings,
                    survey_year=audit_year,
                )
                import_validation_events.purge_expired_attempts(connection)
    except ImportValidationUnavailable:
        raise
    except ImportReferenceError:
        raise ImportValidationUnavailable(
            "system_reference_package_unavailable",
        ) from None
    except (psycopg.Error, OSError, RuntimeError, ValueError, KeyError):
        raise ImportValidationUnavailable("import_validation_unavailable") from None
    return result


def stored_survey_year_counts(connection_factory=get_connection):
    """List successful stored years without exposing source or household records."""
    try:
        with connection_factory() as connection, connection.transaction():
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
            )
            return dataset_batches.validated_survey_year_counts(connection)
    except (psycopg.Error, RuntimeError):
        raise ImportValidationUnavailable("import_validation_unavailable") from None


def record_oversized_attempt(actor_id, *, connection_factory=get_connection):
    """Audit a request rejected before the uploaded streams can be inspected."""
    try:
        with connection_factory() as connection, connection.transaction():
            import_validation_events.record_attempt(
                connection, actor_id, "rejected", None, None,
                {"_result": {
                    "code": "request_too_large",
                    "description": AUDIT_DESCRIPTIONS["request_too_large"],
                    "same_year_reupload": False,
                }}, 1, 0,
                survey_year=None,
            )
            import_validation_events.purge_expired_attempts(connection)
    except (psycopg.Error, RuntimeError):
        raise ImportValidationUnavailable("import_validation_unavailable") from None
