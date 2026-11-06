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


def present_report(report):
    """Expose only fixed wording and aggregate counts from the safe report."""
    if report is None:
        return None

    def issues(severity):
        return [
            {
                "source": SOURCE_LABELS.get(item["source"], "Related records"),
                "message": ISSUE_LABELS.get(item["code"], "This file needs review."),
                "count": item["count"],
            }
            for item in report[severity]
        ]

    return {
        "passed": report["passed"],
        "sources": [
            {"name": SOURCE_LABELS[name], "records": report["datasets"].get(name, {}).get("records")}
            for name in EXCEL_FILES
        ],
        "errors": issues("errors"),
        "warnings": issues("warnings"),
    }
