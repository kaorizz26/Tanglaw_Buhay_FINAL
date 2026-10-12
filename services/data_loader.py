"""Load CBMS sources unchanged and check keys and indicator inputs."""

from pathlib import Path
import re

import pandas as pd

from services.vulnerability.housing import ASSET_FIELDS


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EXCEL_FILES = (
    "household", "section_a_e_indiv", "section_l02_to_l41", "section_l11_to_l14"
)
REFERENCE_FILES = {
    "codebook": "Tanglaw_Buhay_Codebook_Values_FINAL.csv",
    "mpi_mapping": "Tanglaw_Buhay_MPI_Mapping_FinalProbably.csv",
    "definitions": "Tanglaw_Buhay_Variable_Definitions_FINAL.csv",
}
KEYS = {
    "household": ["uuid"],
    "section_a_e_indiv": ["uuid", "line_number"],
    "section_l02_to_l41": ["uuid", "section_l_line_number"],
}
REFERENCE_COLUMNS = {
    "codebook": ["variable_name", "code", "label"],
    "mpi_mapping": ["dimension", "indicator", "variable_name", "code", "value_meaning", "deprived"],
    "definitions": ["variable_name", "display_label", "description", "question", "dataset", "level", "section", "data_type", "provenance"],
}


def load_data(data_dir=DATA_DIR):
    """Return (datasets, references); no recoding, ID creation, or file writes.

    Object dtype preserves Excel cell types. Blank cells stay empty strings and
    strings such as 'NA' are not converted into missing values.
    """
    data_dir = Path(data_dir)
    datasets = {
        name: pd.read_excel(
            data_dir / "raw" / f"{name}.xlsx", sheet_name=name,
            engine="openpyxl", dtype=object, keep_default_na=False,
        )
        for name in EXCEL_FILES
    }
    references = {
        name: pd.read_csv(
            data_dir / "reference" / filename, encoding="utf-8-sig",
            dtype=object, keep_default_na=False,
        )
        for name, filename in REFERENCE_FILES.items()
    }
    return datasets, references


def _invalid_keys(frame, keys):
    return frame[keys].apply(
        lambda column: column.isna() | column.map(
            lambda value: isinstance(value, str) and not value.strip()
        )
    ).any(axis=1)


def _require_keys(frame, keys, unique=False):
    if any(key not in frame.columns for key in keys):
        raise ValueError("Required join columns are missing.")
    if _invalid_keys(frame, keys).any():
        raise ValueError("Join keys contain missing or blank values.")
    if unique and frame.duplicated(keys).any():
        raise ValueError("Join keys are duplicated; join would multiply records.")


def join_households(records, households):
    """Attach household fields to records by uuid, retaining every source row.

    Overlapping household fields get a _household suffix in the result only.
    Neither source frame is modified. An unmatched row remains in the result.
    """
    _require_keys(records, ["uuid"])
    _require_keys(households, ["uuid"], unique=True)
    return records.merge(
        households, on="uuid", how="left", validate="many_to_one",
        suffixes=("", "_household"),
    )


def join_section_l(members, section_l):
    """Attach Section L using its person field, retaining every member row."""
    _require_keys(members, ["uuid", "line_number"], unique=True)
    _require_keys(section_l, ["uuid", "section_l_line_number"], unique=True)
    return members.merge(
        section_l, left_on=["uuid", "line_number"],
        right_on=["uuid", "section_l_line_number"], how="left",
        validate="one_to_one", suffixes=("", "_section_l"),
    )


def _link_counts(records, base, record_keys, base_keys):
    """Count unmatched records in both directions without exposing key values."""
    left = records[record_keys].copy()
    right = base[base_keys].copy()
    right.columns = record_keys
    matched_left = left.merge(
        right.drop_duplicates(), on=record_keys, how="left", indicator=True,
    )
    matched_right = right.merge(
        left.drop_duplicates(), on=record_keys, how="left", indicator=True,
    )
    return {
        "source_records_unmatched": int((matched_left["_merge"] == "left_only").sum()),
        "base_records_without_source": int((matched_right["_merge"] == "left_only").sum()),
    }


def _indicator_fields(expression, definitions):
    """Resolve column references only; never interpret deprivation rules."""
    fields, issues = set(), []
    for token in expression.split(" + "):
        if token == "household member count":
            fields.update({("section_a_e_indiv", "uuid"), ("section_a_e_indiv", "line_number")})
            continue
        if token == "Selected R16 asset fields":
            # Phase 3A explicitly selects these 15 fields; the CSV is unchanged.
            fields.update(("household", field) for field in ASSET_FIELDS)
            defined = set(definitions.loc[definitions["dataset"] == "household", "variable_name"])
            issues.extend(f"Selected asset field lacks a definition: {field}" for field in ASSET_FIELDS if field not in defined)
            continue
        span = re.fullmatch(r"([A-Z])(\d+)-\1(\d+)", token)
        tokens = [token]
        if span:
            letter, first, last = span.groups()
            tokens = [f"{letter}{number:02d}" for number in range(int(first), int(last) + 1)]
        for item in tokens:
            names = definitions["variable_name"]
            mask = names.eq(item)
            if re.fullmatch(r"[A-Z]\d{2}", item):
                mask = names.str.startswith(item.lower() + "_")
            selected = definitions[mask]
            if len(selected) != 1:
                issues.append(f"Unresolved or ambiguous variable reference: {item}")
            else:
                fields.add((selected.iloc[0]["dataset"], selected.iloc[0]["variable_name"]))
    return fields, issues


def verify_data(datasets, references):
    """Return aggregate checks and issues; run joins without saving any data."""
    report = {"datasets": {}, "references": {}, "joins": {}, "indicators": {}, "issues": []}
    for name, frame in datasets.items():
        keys = KEYS.get(name, ["uuid"])
        missing = [key for key in keys if key not in frame.columns]
        stats = {"records": len(frame), "columns": len(frame.columns), "missing_key_columns": missing}
        if not missing:
            stats["invalid_key_records"] = int(_invalid_keys(frame, keys).sum())
            if name in KEYS:
                stats["duplicate_key_records"] = int(frame.duplicated(keys, keep=False).sum())
                stats["duplicate_key_groups"] = len(frame.loc[frame.duplicated(keys, keep=False), keys].drop_duplicates())
            else:
                # Child-death records may legitimately share a household uuid.
                stats["repeated_household_records"] = int(frame.duplicated(keys, keep=False).sum())
            if stats["invalid_key_records"] or stats.get("duplicate_key_records", 0):
                report["issues"].append(f"{name}: invalid or duplicated join keys.")
        else:
            report["issues"].append(f"{name}: missing join columns.")
        report["datasets"][name] = stats

    reference_keys = {"codebook": ["variable_name", "code"], "definitions": ["dataset", "variable_name"], "mpi_mapping": ["dimension", "indicator", "variable_name", "code"]}
    for name, frame in references.items():
        missing = [column for column in REFERENCE_COLUMNS[name] if column not in frame.columns]
        report["references"][name] = {"records": len(frame), "missing_columns": missing}
        if missing:
            report["issues"].append(f"{name}: required reference columns missing.")
        else:
            report["references"][name]["duplicate_key_records"] = int(frame.duplicated(reference_keys[name], keep=False).sum())
            if report["references"][name]["duplicate_key_records"]:
                report["issues"].append(f"{name}: duplicate reference keys need review.")

    household = datasets["household"]
    for name in EXCEL_FILES[1:]:
        try:
            joined = join_households(datasets[name], household)
            stats = _link_counts(datasets[name], household, ["uuid"], ["uuid"])
            stats.update({"join_passed": len(joined) == len(datasets[name]), "joined_records": len(joined)})
            del joined
            report["joins"][f"{name}_to_household"] = stats
        except ValueError:
            report["joins"][f"{name}_to_household"] = {"join_passed": False}
            report["issues"].append(f"{name}: household join failed; check key columns, types and uniqueness.")
    try:
        members, section_l = datasets["section_a_e_indiv"], datasets["section_l02_to_l41"]
        joined = join_section_l(members, section_l)
        stats = _link_counts(section_l, members, ["uuid", "section_l_line_number"], ["uuid", "line_number"])
        stats.update({"join_passed": len(joined) == len(members), "joined_records": len(joined)})
        del joined
        report["joins"]["section_l_to_members"] = stats
    except ValueError:
        report["joins"]["section_l_to_members"] = {"join_passed": False}
        report["issues"].append("Section L member join failed; check key columns, types and uniqueness.")
    for name, stats in report["joins"].items():
        if stats.get("source_records_unmatched", 0):
            report["issues"].append(f"{name}: unmatched source records need review.")

    if not report["references"]["mpi_mapping"]["missing_columns"] and not report["references"]["definitions"]["missing_columns"]:
        mapping = references["mpi_mapping"]
        report["indicator_count"] = int(mapping["indicator"].nunique())
        if report["indicator_count"] != 16:
            report["issues"].append("Expected 16 distinct vulnerability indicators.")
        for indicator, rows in mapping.groupby("indicator", sort=False):
            fields, issues = set(), []
            for expression in rows["variable_name"].drop_duplicates():
                resolved, unresolved = _indicator_fields(expression, references["definitions"])
                fields.update(resolved)
                issues.extend(unresolved)
            missing = [f"{dataset}.{column}" for dataset, column in sorted(fields) if dataset not in datasets or column not in datasets[dataset].columns]
            report["indicators"][indicator] = {"required_columns_checked": len(fields), "missing_columns": missing, "issues": sorted(set(issues))}
            if missing:
                report["issues"].append(f"{indicator}: required columns missing.")
            report["issues"].extend(f"{indicator}: {issue}" for issue in sorted(set(issues)))
    report["loading_and_joins_passed"] = (
        all(stats["join_passed"] and not stats.get("source_records_unmatched", 0) for stats in report["joins"].values())
        and all(not stats["missing_key_columns"] and not stats.get("invalid_key_records", 0) and not stats.get("duplicate_key_records", 0) for stats in report["datasets"].values())
    )
    return report
