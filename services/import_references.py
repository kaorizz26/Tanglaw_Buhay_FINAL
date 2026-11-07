"""Load the approved, batch-scoped reference snapshot for import preflight."""

from hashlib import sha256
from pathlib import Path
import re

import pandas as pd

from repositories import dataset_batches
from services.cbms_preflight import load_reference_csv_bundle
from services.data_loader import REFERENCE_FILES


class ImportReferenceError(RuntimeError):
    """A fixed failure that exposes no reference records or SQL details."""


REFERENCE_FILE_BYTES = 16 * 1024 * 1024


def _reference_version(references):
    """Return the stable hash identity shared by file and database references."""
    version_parts = []
    for name in REFERENCE_FILES:
        digest = references[name].attrs.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ImportReferenceError("approved_references_invalid")
        version_parts.append(f"{name}:{digest}")
    return sha256("|".join(version_parts).encode("ascii")).hexdigest()


def load_system_import_references(reference_dir):
    """Load the deployment-controlled reference package for a first import."""
    try:
        root = Path(reference_dir)
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ImportReferenceError("system_reference_package_missing")
        for filename in REFERENCE_FILES.values():
            path = root / filename
            if (path.is_symlink() or not path.is_file()
                    or path.stat().st_size <= 0
                    or path.stat().st_size > REFERENCE_FILE_BYTES):
                raise ImportReferenceError("system_reference_package_invalid")
        references, report = load_reference_csv_bundle(root)
    except ImportReferenceError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        raise ImportReferenceError("system_reference_package_invalid") from None
    if report["errors"] or set(references) != set(REFERENCE_FILES):
        raise ImportReferenceError("system_reference_package_invalid")
    return references, _reference_version(references)


def load_import_references(connection, reference_batch_id):
    """Return validated reference frames and a stable identity for their version."""
    batch = dataset_batches.find_validated_batch_metadata(
        connection, reference_batch_id,
    )
    if (batch is None or batch["survey_year"] != 2022
            or batch["municipality"] != "Pozorrubio"):
        raise ImportReferenceError("approved_reference_batch_missing")
    columns = batch["source_columns"]
    provenance = batch["source_provenance"]
    if not isinstance(columns, dict) or not isinstance(provenance, dict):
        raise ImportReferenceError("approved_references_invalid")
    files = provenance.get("files")
    if not isinstance(files, dict):
        raise ImportReferenceError("approved_references_invalid")

    references = {}
    for name, filename in REFERENCE_FILES.items():
        names = columns.get(name)
        details = files.get(name)
        if (not isinstance(names, list) or not names
                or any(not isinstance(value, str) or not value.strip() for value in names)
                or len(names) != len(set(names))
                or not isinstance(details, dict)
                or details.get("filename") != filename
                or type(details.get("records")) is not int
                or details["records"] <= 0
                or not isinstance(details.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", details["sha256"])):
            raise ImportReferenceError("approved_references_invalid")
        records = []
        for position, responses in dataset_batches.iter_batch_records(
                connection, reference_batch_id, name):
            if (type(position) is not int or position != len(records) + 1
                    or not isinstance(responses, dict)
                    or set(responses) != set(names)
                    or any(not isinstance(value, str) for value in responses.values())):
                raise ImportReferenceError("approved_references_invalid")
            records.append(responses)
        if len(records) != details["records"]:
            raise ImportReferenceError("approved_references_invalid")
        references[name] = pd.DataFrame(records, columns=names, dtype=object)
        references[name].attrs.update({
            "filename": filename, "sha256": details["sha256"],
        })
    return references, _reference_version(references)
