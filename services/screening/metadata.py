"""Program references, methodology notes and the unchanged result schema."""

PROGRAMS = ("4Ps", "SLP", "SPISC", "TUPAD")
STATUSES = ("For Review", "No Historical CBMS Trigger", "Needs Data Review")
CBMS_QUESTIONNAIRE = "https://psa.gov.ph/system/files/cbms/2022_Household_Profile_Questionnaire.pdf"
SLP_POLICY = "https://dswd-file-assets.s3.ap-southeast-1.amazonaws.com/wp-content/uploads/2025/07/MC-No.-03-S-2025.pdf"
TUPAD_BASE_POLICY = "https://bwsc.dole.gov.ph/wp-content/uploads/2024/04/DO-239-23-Guideline-in-the-Implementation-of-the-Department-of-Labor-and-Employment-Integrated-Livelihood-and-Emergency-Employment-Program-DILEEP.pdf"
REFERENCES = {
    "4Ps": ("https://pantawid.dswd.gov.ph/wp-content/uploads/2026/03/CCTG-ORDER-NO.04-S.2026-REVISED.pdf",),
    "SLP": (SLP_POLICY,),
    "SPISC": ("https://fo11.dswd.gov.ph/5849-2/", "https://www.dswd.gov.ph/issuance/"),
    "TUPAD": (TUPAD_BASE_POLICY, "https://dole.gov.ph/department-orders/"),
}
UNVERIFIED_REQUIREMENTS = {
    "4Ps": ("Official poverty-targeting classification.", "Current qualifying family composition.",
            "Existing beneficiary status and applicable program conditions."),
    "SLP": ("Actual livelihood or employment assistance needs.",
            "Participant suitability, preferences and official SLP assessment."),
    "SPISC": ("Current indigency and financial support.", "Existing pension receipt.",
              "Present-day circumstances and applicable SPISC conditions."),
    "TUPAD": ("Current disadvantaged/displaced worker circumstances.",
              "Current livelihood/employment situation, prior assistance and applicable DOLE conditions."),
}
COMMON_NOTES = (
    "Project screening heuristic, not official eligibility or a program score.",
    "Historical survey evidence; current circumstances are not inferred.",
    "Unverified requirements are informational; no tasks or mandatory follow-up are created.",
)
PROGRAM_NOTES = {
    "4Ps": ("CBMS household membership does not establish a single official 4Ps family unit.",
            "L06=1 adds historical pregnancy review; conflicting L01=2/L06=1 remains unresolved unless an independent child trigger applies.",
            "L07=1 is a provisional dataset-specific Yes interpretation, corroborated by L06=1 and female age 10+; not PSA-confirmed recoding."),
    "SLP": ("EF requires age 18+, B06=7 and recorded E01=2; reference-week nonwork is not official unemployment or MPI imputation.",
            "MD requires age 16+, B06=7, recorded E01=1 and E18=3/4/5/6.",
            "E01=2 excludes MD; E11/E18 are not required for EF. Missing essential age/B06/E01 or applicable working-member E18 remains unresolved unless a known exclusion applies; supported EF/MD evidence takes precedence.",
            "MC 003 s.2025 pp.8-9 provides MD age 16+ and EF age 18+; these work-status triggers are project heuristics."),
    "SPISC": ("Age 60+ is a review signal, not proof of indigency or pension eligibility.",
              "L15=1 is household disability context; only a senior's own joined L16=1 corroborated by L15=1 supports senior-specific disability.",
              "L16=1 is a provisional dataset-specific Yes interpretation, not PSA-confirmed recoding; optional support never changes the age trigger.",
              "The FO XI page describes older guidance; MC 022 s.2026 is listed, but its full text has not been verified."),
    "TUPAD": ("Only members aged 18+ with B06=7 are considered for automatic TUPAD review under the approved project methodology.",
              "Recorded E01=2 establishes reference-week nonwork, not long-term unemployment; E11/E18 are not required for this trigger.",
              "Recorded E01=1 with E11=2/3 establishes seasonal or irregular work; E18 is supporting context, including government/GOCC worker class 2.",
              "Missing or invalid essential age, B06, E01, or applicable working-member E11 remains unresolved; no employment is assumed.",
              "Age 18+ follows DO 239-23 section 8, with no invented upper age limit.",
              "DO 239-E s.2026 is listed, but its full text has not been verified; no new exclusions are invented.",
              "Missing E11 does not erase a supported nonwork trigger; contradictory or genuinely indeterminate evidence remains unresolved."),
}
MD_CLASSES = {
    3: "self-employed without paid employees",
    4: "employer in own family-operated farm/business",
    5: "paid work in own family-operated farm/business",
    6: "unpaid work in own family-operated farm/business",
}
IRREGULAR_WORK = {
    2: "short-term, seasonal or casual work",
    3: "work for different employers/customers day-to-day or week-to-week",
}
OUTPUT_COLUMNS = (
    "household_uuid", "program_code", "screening_status", "trigger_reasons",
    "supporting_evidence", "triggering_member_line_numbers",
    "key_unverified_requirements", "survey_year", "source_references", "methodology_notes",
    "data_quality_warnings",
)
