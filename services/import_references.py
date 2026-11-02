"""Load the approved, batch-scoped reference snapshot for import preflight."""

from hashlib import sha256
import re

import pandas as pd

from repositories import dataset_batches
from services.data_loader import REFERENCE_FILES


class ImportReferenceError(RuntimeError):
    """A fixed failure that exposes no reference records or SQL details."""


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
    version_parts = []
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
        version_parts.append(f"{name}:{details['sha256']}")

    version = sha256("|".join(version_parts).encode("ascii")).hexdigest()
    return references, version
