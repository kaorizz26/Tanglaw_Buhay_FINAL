"""Read-only CSV preflight checks; reports contain fixed labels and counts only."""

import csv
from hashlib import sha256
import io
from pathlib import Path
import re

import pandas as pd

from services.data_loader import (
    DATA_DIR, EXCEL_FILES, KEYS, REFERENCE_FILES, REFERENCE_KEYS,
    _dataset_statistics, _invalid_keys, _reference_statistics,
    _verify_indicator_columns, _verify_joins,
)
from services.vulnerability.direct import build_deprivation_map
from services.vulnerability.health import FIES_FIELDS
from services.vulnerability.housing import ASSET_FIELDS


# These are the variables consumed by existing direct mapping functions.
DIRECT_MAPPING_FIELDS = (
    "q01_main_water", "r10_tenure", "r12_electricity", "n01_internet_acess",
    "h01_access_to_public_transportation", "q14_toilet_facility",
    "q19_share_toilet", "r03_roof", "r04_outer_walls", "r06_floor",
)
# Required source inputs are independent of uploaded reference declarations.
# Reuse engine field groups; this contract contains no scoring or routing rules.
# Join keys are checked separately using KEYS and _dataset_statistics.
REQUIRED_ENGINE_COLUMNS = {
    "household": (
        *DIRECT_MAPPING_FIELDS, *ASSET_FIELDS, *FIES_FIELDS,
        "r07_floor_area", "l33_member_who_has_been_sick",
        "l01_with_member_who_has_been_pregnant", "l06_with_member_currently_pregnant",
        "l15_with_pwd_member",
    ),
    "section_a_e_indiv": (
        "a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment",
        "e18_class_of_worker", "e15_want_more_hours", "c02_hgc_group",
        "c03_currently_attending_school", "a05_sex",
    ),
    "section_l02_to_l41": (
        "l07_currently_pregnant", "l16_pwd", "l34_has_been_sick",
        "l38_availed_medical_treatment",
    ),
}


def validate_csv_bundle(data_dir: str | Path = DATA_DIR) -> dict:
    """Return aggregate errors/warnings for four source and three reference CSVs.

    Files use the existing raw/reference directory layout, with source filenames
    changed to <dataset>.csv. No records, paths, raw headers or parser exceptions
    are returned. Passing preflight permits further import work; it neither
    inserts a batch nor establishes survey provenance or staff authorization.
    """
    _, _, report = load_csv_bundle(data_dir)
    return report


def load_csv_bundle(data_dir: str | Path):
    """Return private source/reference frames and their aggregate preflight report.

    Each file is read once. Frame attrs hold its filename and SHA-256 from the
    same bytes that were parsed, so an importer need not reopen validated files.
    These frames are for backend use only; never return them from public routes.
    """
    root = Path(data_dir)
    report = {
        "passed": False, "errors": [], "warnings": [],
        "datasets": {}, "references": {}, "joins": {},
    }
    datasets = _read_group(
        {name: root / "raw" / f"{name}.csv" for name in EXCEL_FILES},
        report, "datasets",
    )
    references = _read_group(
        {name: root / "reference" / filename for name, filename in REFERENCE_FILES.items()},
        report, "references",
    )
    _check_sources(datasets, report)
    _check_references(references, report)
    if len(datasets) == len(EXCEL_FILES):
        _check_joins(datasets, report)
    ready = all(
        name in references and not report["references"][name]["missing_columns"]
        for name in REFERENCE_FILES
    )
    if ready:
        _check_definition_coverage(datasets, references, report)
        _check_mapping(datasets, references, report)
    report["passed"] = not report["errors"]
    return datasets, references, report


def _issue(report, severity, source, code, count=1):
    """Add a fixed diagnostic code and aggregate count, never source values."""
    report[severity].append({"source": source, "code": code, "count": int(count)})


def _read_group(paths, report, group):
    """Read required files once and collect safe file-level diagnostics."""
    frames = {}
    for name, path in paths.items():
        frame, error = _read_csv(path)
        if error:
            _issue(report, "errors", name, error)
        else:
            frames[name] = frame
            report[group][name] = {"records": len(frame), "columns": len(frame.columns)}
    return frames


def _read_csv(path):
    """Keep every parsed cell as text and reject ambiguous/ragged CSV input."""
    try:
        content = path.read_bytes()
        with io.StringIO(content.decode("utf-8-sig"), newline="") as handle:
            reader = csv.reader(handle, strict=True)
            columns = next(reader, [])
            if not columns or any(not name.strip() for name in columns):
                return None, "empty_or_blank_header"
            if len(set(columns)) != len(columns):
                return None, "duplicate_header"
            rows = []
            for row in reader:
                if len(row) != len(columns):
                    return None, "inconsistent_row_width"
                rows.append(row)
    except FileNotFoundError:
        return None, "missing_file"
    except (OSError, UnicodeError, csv.Error):
        # Parser messages and filesystem paths may expose private source content.
        return None, "unreadable_csv"
    frame = pd.DataFrame(rows, columns=columns, dtype=object)
    frame.attrs.update({"filename": path.name, "sha256": sha256(content).hexdigest()})
    return frame, None


def _check_sources(datasets, report):
    """Reuse source key checks and validate integer keys without rewriting text."""
    for name, frame in datasets.items():
        stats, _ = _dataset_statistics(name, frame)
        # Missing names are trusted join-field constants, not uploaded headers.
        report["datasets"][name].update(stats)
        for field, code in (
            ("missing_key_columns", "missing_join_columns"),
            ("invalid_key_records", "blank_join_keys"),
            ("duplicate_key_records", "duplicate_join_keys"),
        ):
            count = len(stats[field]) if field == "missing_key_columns" else stats.get(field, 0)
            if count:
                _issue(report, "errors", name, code, count)
        if name == "household" and frame.empty:
            _issue(report, "errors", name, "empty_household_dataset")
        missing = sum(field not in frame for field in REQUIRED_ENGINE_COLUMNS.get(name, ()))
        if missing:
            _issue(report, "errors", name, "missing_engine_columns", missing)
        if (
            name in ("section_a_e_indiv", "section_l02_to_l41")
            and not stats["missing_key_columns"]
        ):
            _check_line_numbers(name, frame, report)
        if name == "section_l11_to_l14":
            count = int(frame.duplicated(keep=False).sum())
            if count:
                _issue(report, "warnings", name, "identical_child_death_rows", count)
    households = datasets.get("household")
    members = datasets.get("section_a_e_indiv")
    if households is not None and not households.empty and members is not None and members.empty:
        _issue(report, "errors", "section_a_e_indiv", "empty_household_roster")


def _integer_key(value):
    """Check the migration's integer range; keep the original CSV cell intact."""
    if not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if -(2 ** 31) <= number < 2 ** 31 else None


def _check_line_numbers(name, frame, report):
    """Detect invalid integer identities and collisions using a temporary view."""
    keys = KEYS[name]
    line_field = keys[1]
    numbers = frame[line_field].map(_integer_key)
    invalid = numbers.isna()
    if invalid.any():
        _issue(report, "errors", name, "invalid_integer_line_numbers", invalid.sum())
    # Distinct text identities such as "01" and "1" cannot share one SQL key.
    unique = frame.loc[~invalid, keys].drop_duplicates().copy()
    unique[line_field] = unique[line_field].map(_integer_key)
    collisions = int(unique.duplicated(keys, keep=False).sum())
    if collisions:
        _issue(report, "errors", name, "integer_key_collisions", collisions)


def _check_references(references, report):
    """Reuse finalized reference schemas and require nonblank, unique keys."""
    for name, frame in references.items():
        stats, _ = _reference_statistics(name, frame)
        report["references"][name].update(stats)
        if frame.empty:
            _issue(report, "errors", name, "empty_reference")
        if stats["missing_columns"]:
            _issue(
                report, "errors", name, "missing_reference_columns", len(stats["missing_columns"]),
            )
            continue
        count = int(_invalid_keys(frame, REFERENCE_KEYS[name]).sum())
        if count:
            _issue(report, "errors", name, "blank_reference_keys", count)
        if stats["duplicate_key_records"]:
            _issue(
                report, "errors", name, "duplicate_reference_keys", stats["duplicate_key_records"],
            )


def _check_joins(datasets, report):
    """Orphans block import; absent roster/health evidence remains a warning."""
    joins, _ = _verify_joins(datasets)
    report["joins"] = joins
    for name, stats in joins.items():
        if not stats["join_passed"]:
            _issue(report, "errors", name, "join_failed")
        if stats.get("source_records_unmatched", 0):
            _issue(report, "errors", name, "orphan_records", stats["source_records_unmatched"])
        if name in ("section_a_e_indiv_to_household", "section_l_to_members"):
            count = stats.get("base_records_without_source", 0)
            if count:
                _issue(report, "warnings", name, "missing_related_records", count)


def _check_definition_coverage(datasets, references, report):
    """Use declared source fields as the format contract; retain extra columns."""
    definitions = references["definitions"]
    unknown = ~definitions["dataset"].isin(EXCEL_FILES)
    if unknown.any():
        _issue(report, "errors", "definitions", "unknown_source_datasets", unknown.sum())
    for name, frame in datasets.items():
        declared = set(definitions.loc[definitions["dataset"].eq(name), "variable_name"])
        missing = declared - set(frame.columns)
        extra = set(frame.columns) - declared
        if missing:
            _issue(report, "errors", name, "missing_defined_columns", len(missing))
        if extra:
            _issue(report, "warnings", name, "columns_without_definitions", len(extra))
    known = set(definitions["variable_name"])
    unknown_codes = ~references["codebook"]["variable_name"].isin(known)
    if unknown_codes.any():
        _issue(report, "errors", "codebook", "variables_without_definitions", unknown_codes.sum())
    blank_provenance = definitions["provenance"].str.strip().eq("")
    if blank_provenance.any():
        _issue(report, "warnings", "definitions", "missing_provenance", blank_provenance.sum())


def _check_mapping(datasets, references, report):
    """Resolve existing indicator inputs and compile only engine-used mappings."""
    count, indicators, _ = _verify_indicator_columns(datasets, references)
    report["indicator_count"] = count
    if count != 16:
        _issue(report, "errors", "mpi_mapping", "incorrect_indicator_count")
    missing = sum(len(stats["missing_columns"]) for stats in indicators.values())
    unresolved = sum(len(stats["issues"]) for stats in indicators.values())
    if missing:
        _issue(report, "errors", "mpi_mapping", "missing_indicator_columns", missing)
    if unresolved:
        _issue(report, "errors", "mpi_mapping", "unresolved_indicator_inputs", unresolved)
    invalid = 0
    for field in DIRECT_MAPPING_FIELDS:
        try:
            build_deprivation_map(references["mpi_mapping"], field)
        except ValueError:
            invalid += 1
    if invalid:
        _issue(report, "errors", "mpi_mapping", "invalid_direct_mappings", invalid)
