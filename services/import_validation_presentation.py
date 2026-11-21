"""Turn fixed preflight codes into brief administrator-facing guidance."""

from services.data_loader import EXCEL_FILES


SOURCE_LABELS = {
    "household": "Households",
    "section_a_e_indiv": "Household members",
    "section_l02_to_l41": "Member health records",
    "section_l11_to_l14": "Child death records",
    "section_a_e_indiv_to_household": "Member links",
    "section_l_to_members": "Health record links",
    "section_l02_to_l41_to_household": "Health record links",
    "section_l11_to_l14_to_household": "Child death record links",
    "definitions": "System field definitions",
    "codebook": "System codebook",
    "mpi_mapping": "System indicator mapping",
}

ISSUE_LABELS = {
    "missing_file": "A required file is missing.",
    "empty_or_blank_header": "The CSV header is empty or has a blank column name.",
    "duplicate_header": "The CSV has duplicate column names.",
    "inconsistent_row_width": "Some rows have a different number of columns.",
    "unreadable_csv": "The file cannot be read as a UTF-8 CSV.",
    "missing_join_columns": "Required household or member ID columns are missing.",
    "blank_join_keys": "Some household or member IDs are blank.",
    "duplicate_join_keys": "Some household or member IDs are repeated.",
    "invalid_household_id_format": (
        "Some household IDs are not in the required format. Use lowercase letters "
        "and digits with hyphens, for example 123e4567-e89b-12d3-a456-426614174000."
    ),
    "empty_household_dataset": "The household file has no records.",
    "empty_household_roster": "The member file has no records.",
    "missing_engine_columns": "Columns needed for assessment are missing.",
    "invalid_integer_line_numbers": "Some member line numbers are invalid.",
    "integer_key_collisions": "Some member line numbers identify the same record.",
    "empty_reference": "An approved system reference is empty.",
    "missing_reference_columns": "An approved system reference is incomplete.",
    "blank_reference_keys": "An approved system reference has blank keys.",
    "duplicate_reference_keys": "An approved system reference has repeated keys.",
    "join_failed": "Related records could not be checked.",
    "orphan_records": "Some records have no matching household or member.",
    "missing_related_records": "Some households or members have no related records.",
    "unknown_source_datasets": "System definitions name an unsupported source.",
    "missing_defined_columns": "Columns expected by system definitions are missing.",
    "columns_without_definitions": "Some extra columns have no system definition.",
    "variables_without_definitions": "System codebook entries have no field definition.",
    "missing_provenance": "Some system definitions lack provenance.",
    "incorrect_indicator_count": "The system indicator mapping is incomplete.",
    "missing_indicator_columns": "Columns needed by the indicator mapping are missing.",
    "unresolved_indicator_inputs": "Some indicator inputs cannot be resolved.",
    "invalid_direct_mappings": "Some indicator mappings cannot be applied.",
    "identical_child_death_rows": "Some child death records are identical.",
}

SECTION_L_COVERAGE_NOTE = ("section_l_to_members", "missing_related_records")


def present_report(report):
    """Expose only fixed wording and aggregate counts from the safe report."""
    if report is None:
        return None

    def present_issue(item):
        columns = item.get("columns", [])
        safe_columns = (
            columns
            if (item.get("code") == "missing_defined_columns"
                and isinstance(columns, list)
                and all(isinstance(column, str) for column in columns))
            else []
        )
        message = ISSUE_LABELS.get(item["code"], "This file needs review.")
        if safe_columns:
            message = (
                "Missing required column:"
                if len(safe_columns) == 1 else "Missing required columns:"
            )
        return {
            "source": SOURCE_LABELS.get(item["source"], "Related records"),
            "message": message,
            "count": item["count"],
            "columns": safe_columns,
        }

    warning_items = report["warnings"]
    coverage_items = [
        item for item in warning_items
        if (item.get("source"), item.get("code")) == SECTION_L_COVERAGE_NOTE
    ]
    review_warning_items = [
        item for item in warning_items
        if (item.get("source"), item.get("code")) != SECTION_L_COVERAGE_NOTE
    ]

    presented_errors = [present_issue(item) for item in report["errors"]]
    presented_warnings = [present_issue(item) for item in review_warning_items]
    coverage_notes = [{
        "source": "Member health records",
        "message": (
            "Household members without a matching row in "
            "section_l02_to_l41.csv. This does not block import."
        ),
        "count": item["count"],
        "columns": [],
    } for item in coverage_items]
    sources = []
    for name in EXCEL_FILES:
        label = SOURCE_LABELS[name]
        source_errors = sum(1 for issue in presented_errors if issue["source"] == label)
        source_warnings = sum(1 for issue in presented_warnings if issue["source"] == label)
        sources.append({
            "name": label,
            "filename": f"{name}.csv",
            "records": report["datasets"].get(name, {}).get("records"),
            "errors": source_errors,
            "warnings": source_warnings,
        })

    return {
        "passed": report["passed"],
        "sources": sources,
        "errors": presented_errors,
        "warnings": presented_warnings,
        "coverage_notes": coverage_notes,
    }


def present_import_result(report, batch_id):
    """Summarize an atomically stored bundle without implying partial imports."""
    if not batch_id or not report or not report.get("passed"):
        return None
    records = [source.get("records") for source in report.get("sources", [])]
    if len(records) != len(EXCEL_FILES) or any(type(value) is not int for value in records):
        return None
    return {
        "imported": sum(records),
        "duplicate_or_skipped": 0,
        "rejected": 0,
        "validation_errors": 0,
    }
