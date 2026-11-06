"""Curated aggregate statistics proposed for future public program pages.

This registry is intentionally separate from household assessment data. A record
is available to public templates only after source verification and explicit
public-display approval.
"""

VERIFIED = "verified"
PENDING_VERIFICATION = "pending_verification"
MISSING_SOURCE = "missing_source"
UNAVAILABLE = "unavailable"

OVERVIEW = "overview"
DEMOGRAPHICS = "demographics"
ACTIVITIES = "activities"
ACHIEVEMENTS = "achievements"

COVERAGE = "coverage"
ACTIVITY = "activity"
ACHIEVEMENT = "achievement"
PROGRAM_INFORMATION = "program_information"

EXACT = "exact"
APPROXIMATE = "approximate"
AT_LEAST = "at_least"

STANDALONE = "standalone"
COMPARABLE_WITHIN_SOURCE = "comparable_within_source"
NOT_DIRECTLY_COMPARABLE = "not_directly_comparable"
OVERLAPPING_GROUPS = "overlapping_groups"

VERIFIED_ON = "2026-11-06"

PUBLIC_STATISTIC_SOURCES = {
    "4ps_q4_2023": {
        "agency": "Department of Social Welfare and Development",
        "title": "4Ps Fourth Quarter Accomplishment Report 2023",
        "url": (
            "https://pantawid.dswd.gov.ph/wp-content/uploads/2024/02/"
            "4Ps-Fourth-Quarter-Accomplishment-Report.pdf"
        ),
    },
    "4ps_q4_2025": {
        "agency": "Department of Social Welfare and Development",
        "title": "4Ps Fourth Quarter Program Implementation Report 2025",
        "url": (
            "https://pantawid.dswd.gov.ph/wp-content/uploads/2026/02/"
            "4th-QUARTER-PIR_2025_6_2_26.pdf"
        ),
    },
    "4ps_region1_swdi_2025": {
        "agency": "DSWD Field Office I",
        "title": "From 4Ps to Public Servant: One of 22K 4Ps Families Now Self-Sufficient",
        "url": (
            "https://fo1.dswd.gov.ph/2026/01/from-4ps-to-public-servant-"
            "one-of-22k-4ps-families-now-self-sufficient/"
        ),
    },
    "4ps_region1_topnotchers_2026": {
        "agency": "Philippine Information Agency",
        "title": "From cash grants to careers: 23 Ilocos 4Ps scholars excel in licensure exams",
        "url": (
            "https://pia.gov.ph/news/from-cash-grants-to-careers-23-ilocos-"
            "4ps-scholars-excel-in-licensure-exams/"
        ),
    },
    "4ps_region1_alkansssya_2026": {
        "agency": "DSWD Field Office I",
        "title": "114 4Ps Families sa Region 1, Future-Ready na dahil sa AlkanSSSya!",
        "url": (
            "https://fo1.dswd.gov.ph/2026/10/114-4ps-families-sa-region-1-"
            "future-ready-na-dahil-sa-alkansssya/"
        ),
    },
    "pozorrubio_4ps_ordinance_2025": {
        "agency": "Provincial Government of Pangasinan",
        "title": "Resolution No. 188-2026",
        "url": "https://www.pangasinan.gov.ph/wp-content/uploads/2026/02/188-2026.pdf",
    },
    "spisc_region1_2026": {
        "agency": "DSWD Field Office I",
        "title": (
            "Mahigit 16K Indigent Senior Citizens sa Region 1, Kabilang na "
            "sa Social Pension Program"
        ),
        "url": (
            "https://fo1.dswd.gov.ph/2026/09/mahigit-16k-indigent-senior-"
            "citizens-sa-region-1-kabilang-na-sa-social-pension-program/"
        ),
    },
    "spisc_philippines_2025": {
        "agency": "Department of Social Welfare and Development",
        "title": "4.1M indigent seniors receive DSWD's social pension in 2025",
        "url": (
            "https://www.dswd.gov.ph/4-1m-indigent-seniors-receive-dswds-"
            "social-pension-in-2025/"
        ),
    },
    "slp_philippines_2026": {
        "agency": "Department of Social Welfare and Development",
        "title": (
            "DSWD's Sustainable Livelihood Program marks 15th year with over "
            "4.5 million beneficiaries nationwide"
        ),
        "url": (
            "https://www.dswd.gov.ph/dswds-sustainable-livelihood-program-"
            "marks-15th-year-with-over-4-5-million-beneficiaries-nationwide/"
        ),
    },
    "slp_pangasinan_2025": {
        "agency": "DSWD Field Office I",
        "title": "Empowering Livelihoods: 91 SLP Associations Receive 38M Seed Capital Fund",
        "url": (
            "https://fo1.dswd.gov.ph/2025/03/empowering-livelihoods-91-slp-"
            "associations-receive-38m-seed-capital-fund/"
        ),
    },
    "tupad_region1_2025": {
        "agency": "Department of Labor and Employment Regional Office I",
        "title": (
            "DOLE's TUPAD Provides P862.5M in Emergency Employment "
            "Assistance to Over 157,000 Workers in Region 1"
        ),
        "url": (
            "https://ro1.dole.gov.ph/news/%F0%9D%90%83%F0%9D%90%8E%F0%9D%90%8B"
            "%F0%9D%90%84%F0%9D%90%AC-%F0%9D%90%93%F0%9D%90%94%F0%9D%90%8F"
            "%F0%9D%90%80%F0%9D%90%83-%F0%9D%90%8F%F0%9D%90%AB%F0%9D%90%A8"
            "%F0%9D%90%AF%F0%9D%90%A2/"
        ),
    },
    "tupad_philippines_2026": {
        "agency": "Department of Labor and Employment",
        "title": (
            "DOLE provides livelihood, emergency jobs to over 760K vulnerable "
            "workers in first half of 2026"
        ),
        "url": (
            "https://dole.gov.ph/news/dole-provides-livelihood-emergency-jobs-"
            "to-over-760k-vulnerable-workers-in-first-half-of-2026/"
        ),
    },
}


def _record(
        record_id,
        program_slug,
        geography,
        category,
        classification,
        title,
        value,
        unit,
        definition,
        reporting_period,
        period_type,
        source_id,
        source_locator,
        *,
        verification_status=VERIFIED,
        value_qualifier=EXACT,
        comparability=STANDALONE,
        limitations=(),
        related_record_id=None,
        supplied_value=None):
    """Build one consistently shaped aggregate statistic record."""

    return {
        "id": record_id,
        "program_slug": program_slug,
        "geography": geography,
        "category": category,
        "classification": classification,
        "title": title,
        "value": value,
        "unit": unit,
        "definition": definition,
        "reporting_period": reporting_period,
        "period_type": period_type,
        "source_id": source_id,
        "source_locator": source_locator,
        "verification_status": verification_status,
        "verified_on": VERIFIED_ON if verification_status == VERIFIED else None,
        "value_qualifier": value_qualifier,
        "comparability": comparability,
        "limitations": tuple(limitations),
        "related_record_id": related_record_id,
        "supplied_value": supplied_value,
        "approved_for_public_display": False,
    }


PUBLIC_STATISTIC_RECORDS = (
    _record(
        "4ps-pozorrubio-active-households-2023", "4ps", "pozorrubio",
        OVERVIEW, COVERAGE, "Active beneficiary households", 2687,
        "households", "Active 4Ps beneficiary households in Pozorrubio.",
        "2023-12-31", "point_in_time", "4ps_q4_2023",
        "Appendix 1, Pozorrubio row, PDF page 43",
    ),
    _record(
        "4ps-pangasinan-active-households-2024", "4ps", "pangasinan",
        OVERVIEW, COVERAGE, "Active beneficiary households", 145564,
        "households", "Proposed active 4Ps household count for Pangasinan.",
        "2024-09", "point_in_time", None, None,
        verification_status=MISSING_SOURCE,
        comparability=NOT_DIRECTLY_COMPARABLE,
        limitations=(
            "The exact primary report was not located.",
            "Do not compare directly with the November 2025 Region I count.",
        ),
    ),
    _record(
        "4ps-pangasinan-self-sufficient-households-2025", "4ps",
        "pangasinan", ACHIEVEMENTS, ACHIEVEMENT,
        "Households assessed as self-sufficient", 16119, "households",
        "Proposed Pangasinan subset of the 2025 Region I SWDI result.",
        "2025", "annual", None, None,
        verification_status=MISSING_SOURCE,
        limitations=(
            "The Region I total is verified, but this provincial subset was "
            "not found in the cited source.",
        ),
        related_record_id="4ps-region1-self-sufficient-households-2025",
    ),
    _record(
        "4ps-region1-active-households-2025", "4ps", "region",
        OVERVIEW, COVERAGE, "Active beneficiary households", 144939,
        "households", "Active 4Ps household beneficiaries in Region I.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Table 2, PDF page 7",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-region1-children-0-18-2025", "4ps", "region",
        DEMOGRAPHICS, COVERAGE, "Children aged 0 to 18", 345519,
        "children", "4Ps household children aged 0 to 18 in Region I.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Table 4, PDF page 11",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-region1-regular-curriculum-children-2025", "4ps", "region",
        DEMOGRAPHICS, COVERAGE, "Regular-curriculum K-12 children", 269529,
        "children", "Region I children enrolled in the regular K-12 curriculum.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Table 6, PDF page 13",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-region1-senior-members-2025", "4ps", "region",
        DEMOGRAPHICS, COVERAGE, "Senior citizen household members", 38153,
        "members", "Senior citizen members of Region I 4Ps households.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Table 8, PDF page 15",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    *(
        _record(
            f"4ps-region1-children-age-{key}-2025", "4ps", "region",
            DEMOGRAPHICS, COVERAGE, f"Children aged {label}", value,
            "children", f"Region I 4Ps household children aged {label}.",
            "2025-11", "point_in_time", "4ps_q4_2025",
            "Table 4, PDF page 11", comparability=COMPARABLE_WITHIN_SOURCE,
        )
        for key, label, value in (
            ("0-2", "0 to 2", 16142),
            ("3-5", "3 to 5", 25910),
            ("6-14", "6 to 14", 186740),
            ("15-18", "15 to 18", 116727),
        )
    ),
    *(
        _record(
            f"4ps-region1-education-{key}-2025", "4ps", "region",
            DEMOGRAPHICS, COVERAGE, label, value, "children",
            f"Region I 4Ps children in {label.lower()}.", "2025-11",
            "point_in_time", "4ps_q4_2025", "Table 5, PDF page 12",
            comparability=COMPARABLE_WITHIN_SOURCE,
        )
        for key, label, value in (
            ("kindergarten", "Kindergarten", 4723),
            ("elementary", "Elementary", 103424),
            ("junior-high", "Junior high school", 106170),
            ("senior-high", "Senior high school", 55212),
        )
    ),
    *(
        _record(
            f"4ps-region1-geography-{key}-2025", "4ps", "region",
            DEMOGRAPHICS, COVERAGE, label, value, "households",
            f"Region I active 4Ps households classified as {label}.",
            "2025-11", "point_in_time", "4ps_q4_2025",
            "Table 3, PDF page 8", comparability=COMPARABLE_WITHIN_SOURCE,
        )
        for key, label, value in (
            ("rural", "Rural", 109312),
            ("urban", "Urban", 29726),
            ("gida", "Geographically isolated and disadvantaged areas", 5901),
        )
    ),
    _record(
        "4ps-philippines-active-households-2025", "4ps", "philippines",
        OVERVIEW, COVERAGE, "Active beneficiary households", 3076256,
        "households", "Active 4Ps household beneficiaries nationwide.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Table 2, PDF page 7",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-philippines-female-grantees-2025", "4ps", "philippines",
        DEMOGRAPHICS, COVERAGE, "Female grantees", 2646582, "grantees",
        "Female designated grantees of active 4Ps households nationwide.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Figure 1, PDF page 9",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-philippines-male-grantees-2025", "4ps", "philippines",
        DEMOGRAPHICS, COVERAGE, "Male grantees", 429674, "grantees",
        "Male designated grantees of active 4Ps households nationwide.",
        "2025-11", "point_in_time", "4ps_q4_2025", "Figure 1, PDF page 9",
        comparability=COMPARABLE_WITHIN_SOURCE,
    ),
    _record(
        "4ps-region1-self-sufficient-households-2025", "4ps", "region",
        ACHIEVEMENTS, ACHIEVEMENT, "Households assessed as self-sufficient",
        22097, "households",
        "Region I 4Ps households assessed at the self-sufficient SWDI level.",
        "2025", "annual", "4ps_region1_swdi_2025", "Opening paragraph",
    ),
    _record(
        "4ps-region1-licensure-topnotchers-2017-2026", "4ps", "region",
        ACHIEVEMENTS, ACHIEVEMENT, "Licensure examination topnotchers", 23,
        "people",
        "Region I 4Ps family members reported as licensure examination "
        "topnotchers since 2017.",
        "2017-2026-04-30", "cumulative", "4ps_region1_topnotchers_2026",
        "Opening and provincial breakdown",
    ),
    _record(
        "4ps-pangasinan-licensure-topnotchers-2017-2026", "4ps",
        "pangasinan", ACHIEVEMENTS, ACHIEVEMENT,
        "Licensure examination topnotchers", 8, "people",
        "Pangasinan subset of Region I 4Ps family members reported as "
        "licensure examination topnotchers since 2017.",
        "2017-2026-04-30", "cumulative", "4ps_region1_topnotchers_2026",
        "Provincial breakdown",
        related_record_id="4ps-region1-licensure-topnotchers-2017-2026",
    ),
    _record(
        "4ps-region1-alkansssya-participants-2026", "4ps", "region",
        ACTIVITIES, ACTIVITY, "AlkanSSSya pilot participants", 114,
        "participants",
        "4Ps beneficiaries who voluntarily registered during the Region I "
        "pilot implementation.",
        "2026-03-25/2026-10-02", "reporting_interval",
        "4ps_region1_alkansssya_2026", "Pilot implementation summary",
        limitations=("Participation is an activity, not a verified impact.",),
    ),
    _record(
        "4ps-pozorrubio-exiting-family-ordinance-2025", "4ps",
        "pozorrubio", ACTIVITIES, ACTIVITY,
        "Local support ordinance for exiting and graduating families", None,
        "initiative",
        "Municipal Ordinance No. 05-11242025 establishes local support for "
        "exiting and graduating 4Ps beneficiaries.",
        "2025", "initiative", "pozorrubio_4ps_ordinance_2025",
        "Resolution No. 188-2026",
        limitations=(
            "This is a local government initiative, not a measured "
            "beneficiary outcome.",
        ),
    ),
    _record(
        "spisc-region1-active-pensioners-2026", "spisc", "region",
        OVERVIEW, COVERAGE, "Active social pensioners", 201142,
        "pensioners", "Active SPISC beneficiaries in Region I.",
        "2026-09-23", "point_in_time", "spisc_region1_2026",
        "Opening paragraph", supplied_value=201170,
        limitations=(
            "The supplied note stated 201,170; the cited official article "
            "states 201,142.",
        ),
    ),
    _record(
        "spisc-region1-new-pensioners-2026", "spisc", "region",
        ACTIVITIES, ACTIVITY, "Newly enrolled pensioners", 16263,
        "pensioners", "Region I pensioners newly added during 2026.",
        "2026 through 2026-09-23", "year_to_date", "spisc_region1_2026",
        "Opening paragraph",
        related_record_id="spisc-region1-active-pensioners-2026",
        limitations=("This count is included in the active total and must not be added to it.",),
    ),
    _record(
        "spisc-philippines-recipients-2025", "spisc", "philippines",
        OVERVIEW, COVERAGE, "Social pension recipients", 4112895,
        "pensioners", "Indigent senior citizens who received social pension during 2025.",
        "2025", "annual", "spisc_philippines_2025", "National result",
    ),
    _record(
        "spisc-monthly-entitlement-2025", "spisc", "philippines",
        OVERVIEW, PROGRAM_INFORMATION, "Monthly pension entitlement", 1000,
        "pesos", "Monthly stipend for a qualified indigent senior citizen.",
        "2025", "entitlement", "spisc_philippines_2025", "Pension amount",
    ),
    _record(
        "spisc-quarterly-equivalent-2025", "spisc", "philippines",
        OVERVIEW, PROGRAM_INFORMATION, "Three-month pension equivalent", 3000,
        "pesos", "Three months of the P1,000 monthly entitlement.",
        "2025", "entitlement", "spisc_philippines_2025",
        "Monthly amount and quarterly distribution statement",
        related_record_id="spisc-monthly-entitlement-2025",
        limitations=("Actual payout schedules depend on official implementation arrangements.",),
    ),
    _record(
        "slp-philippines-cumulative-participants-2011-2026", "slp",
        "philippines", OVERVIEW, COVERAGE,
        "Cumulative beneficiaries and participants", 4530000, "participants",
        "SLP household beneficiaries and participants served nationwide since 2011.",
        "2011-2026-05", "cumulative", "slp_philippines_2026",
        "National cumulative result", value_qualifier=APPROXIMATE,
        comparability=OVERLAPPING_GROUPS,
    ),
    _record(
        "slp-philippines-microenterprise-beneficiaries-2011-2026", "slp",
        "philippines", OVERVIEW, ACTIVITY,
        "Microenterprise Development beneficiaries", 3760000, "beneficiaries",
        "Beneficiaries assisted under the Microenterprise Development track since 2011.",
        "2011-2026-05", "cumulative", "slp_philippines_2026",
        "Microenterprise Development track", value_qualifier=AT_LEAST,
        comparability=OVERLAPPING_GROUPS,
        limitations=(
            "Do not assume this and the Employment Facilitation count are "
            "mutually exclusive or exhaustive.",
        ),
    ),
    _record(
        "slp-philippines-employment-facilitation-beneficiaries-2011-2026",
        "slp", "philippines", OVERVIEW, ACTIVITY,
        "Employment Facilitation beneficiaries", 450769, "beneficiaries",
        "Beneficiaries receiving Employment Facilitation interventions since 2011.",
        "2011-2026-05", "cumulative", "slp_philippines_2026",
        "Employment Facilitation track", comparability=OVERLAPPING_GROUPS,
        limitations=("Do not sum automatically with the Microenterprise Development count.",),
    ),
    _record(
        "slp-philippines-microenterprise-grantees-2026", "slp",
        "philippines", ACTIVITIES, ACTIVITY,
        "Microenterprise grantees served in 2026", 154495, "grantees",
        "Microenterprise grantees served during 2026 as reported on June 25.",
        "2026 through 2026-06-25", "year_to_date", "slp_philippines_2026",
        "2026 Microenterprise Development result",
        comparability=NOT_DIRECTLY_COMPARABLE,
        limitations=("Keep separate from the cumulative figures since 2011.",),
    ),
    _record(
        "slp-pangasinan-project-participants-2025", "slp", "pangasinan",
        ACTIVITIES, ACTIVITY, "Specific livelihood project participants", 1984,
        "participants",
        "Participants organized into SLP associations for the Rosales "
        "Agricultural Trading Center initiative.",
        "2025-03-26", "project", "slp_pangasinan_2025", "Project summary",
        limitations=("This is a specific initiative, not a province-wide SLP total.",),
    ),
    _record(
        "slp-pangasinan-project-associations-2025", "slp", "pangasinan",
        ACTIVITIES, ACTIVITY, "Livelihood associations in the project", 91,
        "associations", "SLP associations participating in the reported Rosales initiative.",
        "2025-03-26", "project", "slp_pangasinan_2025", "Project summary",
        related_record_id="slp-pangasinan-project-participants-2025",
        limitations=("This is a specific initiative, not a province-wide SLP total.",),
    ),
    _record(
        "slp-pangasinan-project-seed-capital-2025", "slp", "pangasinan",
        ACTIVITIES, ACTIVITY, "Seed capital for the livelihood project", 38000000,
        "pesos", "Seed capital provided for the reported Rosales initiative.",
        "2025-03-26", "project", "slp_pangasinan_2025", "Project summary",
        value_qualifier=APPROXIMATE,
        related_record_id="slp-pangasinan-project-participants-2025",
        limitations=(
            "This is a specific initiative, not province-wide SLP spending.",
            "Receiving seed capital does not prove enterprise success.",
        ),
    ),
    _record(
        "tupad-region1-workers-assisted-2025", "tupad", "region",
        OVERVIEW, ACTIVITY, "Workers assisted", 157342, "workers",
        "Disadvantaged workers receiving temporary employment through TUPAD in Region I.",
        "2025-01/2025-11", "reporting_interval", "tupad_region1_2025",
        "Regional accomplishment summary", comparability=COMPARABLE_WITHIN_SOURCE,
        limitations=("Temporary employment must not be described as permanent employment.",),
    ),
    _record(
        "tupad-region1-wages-released-2025", "tupad", "region",
        OVERVIEW, ACTIVITY, "Wages released", 862500000, "pesos",
        "Approximate TUPAD wages released in Region I.",
        "2025-01/2025-11", "reporting_interval", "tupad_region1_2025",
        "Regional accomplishment summary", value_qualifier=APPROXIMATE,
        related_record_id="tupad-region1-workers-assisted-2025",
    ),
    *(
        _record(
            f"tupad-{key}-workers-assisted-2025", "tupad", geography,
            OVERVIEW, ACTIVITY, f"Workers assisted in {label}", value,
            "workers", f"Workers assisted through TUPAD in {label}.",
            "2025-01/2025-11", "reporting_interval", "tupad_region1_2025",
            "Provincial breakdown", comparability=COMPARABLE_WITHIN_SOURCE,
            related_record_id="tupad-region1-workers-assisted-2025",
        )
        for key, geography, label, value in (
            ("pangasinan", "pangasinan", "Pangasinan", 63086),
            ("ilocos-norte", "region", "Ilocos Norte", 56186),
            ("la-union", "region", "La Union", 23131),
            ("ilocos-sur", "region", "Ilocos Sur", 14939),
        )
    ),
    _record(
        "tupad-philippines-workers-assisted-2026", "tupad", "philippines",
        OVERVIEW, ACTIVITY, "Workers assisted", 734364, "workers",
        "Workers receiving temporary employment through TUPAD nationwide.",
        "2026-01/2026-06", "reporting_interval", "tupad_philippines_2026",
        "TUPAD national result", comparability=NOT_DIRECTLY_COMPARABLE,
        limitations=("The reporting period differs from the 2025 Region I figures.",),
    ),
    _record(
        "tupad-philippines-assistance-2026", "tupad", "philippines",
        OVERVIEW, ACTIVITY, "TUPAD assistance", 5550000000, "pesos",
        "Approximate assistance for nationwide TUPAD temporary employment.",
        "2026-01/2026-06", "reporting_interval", "tupad_philippines_2026",
        "TUPAD national result", value_qualifier=APPROXIMATE,
        comparability=NOT_DIRECTLY_COMPARABLE,
        related_record_id="tupad-philippines-workers-assisted-2026",
    ),
)


def _gap(program_slug, geography, category, status, reason):
    """Build one explicit unavailable or pending-data entry."""

    return {
        "program_slug": program_slug,
        "geography": geography,
        "category": category,
        "status": status,
        "reason": reason,
    }


PUBLIC_STATISTIC_GAPS = (
    _gap(
        "4ps", "pozorrubio", DEMOGRAPHICS, UNAVAILABLE,
        "No verified municipal beneficiary demographic breakdown was supplied.",
    ),
    _gap(
        "4ps", "pangasinan", DEMOGRAPHICS, UNAVAILABLE,
        "No verified provincial beneficiary demographic breakdown was supplied.",
    ),
    _gap(
        "4ps", "pangasinan", OVERVIEW, PENDING_VERIFICATION,
        "The two proposed provincial figures still need exact primary sources.",
    ),
    _gap(
        "4ps", None, OVERVIEW, UNAVAILABLE,
        "A comparable historical series is not yet available.",
    ),
    _gap(
        "spisc", "pozorrubio", OVERVIEW, UNAVAILABLE,
        "No verified municipal SPISC coverage figure was supplied.",
    ),
    _gap(
        "spisc", "pangasinan", OVERVIEW, UNAVAILABLE,
        "No verified provincial SPISC coverage figure was supplied.",
    ),
    _gap(
        "spisc", None, DEMOGRAPHICS, UNAVAILABLE,
        "No verified age or sex breakdown was supplied.",
    ),
    _gap(
        "spisc", None, ACHIEVEMENTS, UNAVAILABLE,
        "No documented beneficiary welfare outcome was supplied.",
    ),
    _gap(
        "slp", "pozorrubio", OVERVIEW, UNAVAILABLE,
        "No verified municipal SLP coverage figure was supplied.",
    ),
    _gap(
        "slp", "region", OVERVIEW, UNAVAILABLE,
        "No verified Region I SLP total was supplied.",
    ),
    _gap(
        "slp", None, DEMOGRAPHICS, UNAVAILABLE,
        "No verified participant demographic breakdown was supplied.",
    ),
    _gap(
        "slp", None, ACHIEVEMENTS, UNAVAILABLE,
        "No verified enterprise, employment, or income outcome was supplied.",
    ),
    _gap(
        "tupad", "pozorrubio", OVERVIEW, UNAVAILABLE,
        "No verified municipal TUPAD coverage figure was supplied.",
    ),
    _gap(
        "tupad", None, DEMOGRAPHICS, UNAVAILABLE,
        "No verified worker demographic breakdown was supplied.",
    ),
    _gap(
        "tupad", None, ACHIEVEMENTS, UNAVAILABLE,
        "No verified sustained employment or community outcome was supplied.",
    ),
)


def get_public_statistics(
        program_slug,
        *,
        geography=None,
        category=None,
        approved_only=True):
    """Return curated records matching the requested public program scope."""

    records = (
        record for record in PUBLIC_STATISTIC_RECORDS
        if record["program_slug"] == program_slug
    )
    if geography is not None:
        records = (
            record for record in records
            if record["geography"] == geography
        )
    if category is not None:
        records = (
            record for record in records
            if record["category"] == category
        )
    if approved_only:
        records = (
            record for record in records
            if record["approved_for_public_display"]
        )
    return tuple(records)


def validate_public_statistic_registry():
    """Return human-readable integrity errors for the curated registry."""

    errors = []
    valid_programs = {"4ps", "spisc", "slp", "tupad"}
    valid_geographies = {"pozorrubio", "pangasinan", "region", "philippines"}
    valid_categories = {OVERVIEW, DEMOGRAPHICS, ACTIVITIES, ACHIEVEMENTS}
    valid_classifications = {COVERAGE, ACTIVITY, ACHIEVEMENT, PROGRAM_INFORMATION}
    valid_statuses = {VERIFIED, PENDING_VERIFICATION, MISSING_SOURCE}
    record_ids = {record["id"] for record in PUBLIC_STATISTIC_RECORDS}

    if len(record_ids) != len(PUBLIC_STATISTIC_RECORDS):
        errors.append("Statistic record IDs must be unique.")

    for record in PUBLIC_STATISTIC_RECORDS:
        prefix = record["id"]
        if record["program_slug"] not in valid_programs:
            errors.append(f"{prefix}: unknown program slug.")
        if record["geography"] not in valid_geographies:
            errors.append(f"{prefix}: unknown geography.")
        if record["category"] not in valid_categories:
            errors.append(f"{prefix}: unknown category.")
        if record["classification"] not in valid_classifications:
            errors.append(f"{prefix}: unknown classification.")
        if record["verification_status"] not in valid_statuses:
            errors.append(f"{prefix}: unknown verification status.")
        if record["value"] is None and record["unit"] != "initiative":
            errors.append(f"{prefix}: only initiative records may omit a value.")
        if record["verification_status"] == VERIFIED:
            if record["source_id"] not in PUBLIC_STATISTIC_SOURCES:
                errors.append(f"{prefix}: verified record lacks a registered source.")
            if not record["source_locator"]:
                errors.append(f"{prefix}: verified record lacks a source locator.")
            if not record["verified_on"]:
                errors.append(f"{prefix}: verified record lacks a verification date.")
        if record["approved_for_public_display"]:
            if record["verification_status"] != VERIFIED:
                errors.append(f"{prefix}: only verified records may be approved.")
            if not record["source_id"]:
                errors.append(f"{prefix}: approved record lacks a source.")
        related_id = record["related_record_id"]
        if related_id and related_id not in record_ids:
            errors.append(f"{prefix}: related record does not exist.")

    return tuple(errors)
