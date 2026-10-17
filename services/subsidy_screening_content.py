"""Staff wording and bounded source fields for stored subsidy findings."""

PROGRAM_NAMES = {
    "4Ps": "Pantawid Pamilyang Pilipino Program",
    "SLP": "Sustainable Livelihood Program",
    "SPISC": "Social Pension for Indigent Senior Citizens",
    "TUPAD": "Tulong Panghanapbuhay sa Ating Disadvantaged / Displaced Workers",
}

STATUS_EXPLANATIONS = {
    "For Review": (
        "Recorded circumstances support considering this household for program review. "
        "This finding does not start a staff review or confirm eligibility."
    ),
    "No Historical CBMS Trigger": (
        "No supported historical screening match was identified in the available "
        "information. This does not mean the household is ineligible."
    ),
    "Needs Data Review": (
        "Missing or conflicting information prevented a clear screening result. "
        "Missing answers do not mean No."
    ),
}

SLP_UNRESOLVED_NOTE = (
    "One or more CBMS pathways remain unresolved; supported EF/MD evidence "
    "still warrants review."
)

FIELD_LABELS = {
    "a07_age": "Age at survey",
    "a02_relation_to_hh_head": "Relationship to household head",
    "a05_sex": "Recorded sex",
    "b06_ofi": "Overseas Filipino status",
    "c07_tvet_graduate": "Completed technical or vocational training",
    "c08_tvet_currently_attending": "Attending technical or vocational training",
    "e01_employed": "Worked at least one hour in the past week",
    "e11_nature_of_employment": "Nature of work",
    "e18_class_of_worker": "Type of worker",
    "l01_with_member_who_has_been_pregnant": "Household reported a member who had been pregnant",
    "l06_with_member_currently_pregnant": "Household reported a pregnant member at survey",
    "l07_currently_pregnant": "Member pregnancy response",
    "l15_with_pwd_member": "Household reported a member with disability",
    "l16_pwd": "Member disability response",
}

HOUSEHOLD_FIELDS = (
    "l01_with_member_who_has_been_pregnant",
    "l06_with_member_currently_pregnant", "l15_with_pwd_member",
)
MEMBER_FIELDS = (
    "a02_relation_to_hh_head", "a05_sex", "e11_nature_of_employment",
    "e18_class_of_worker", "c07_tvet_graduate", "c08_tvet_currently_attending",
)
HEALTH_FIELDS = ("l07_currently_pregnant", "l16_pwd")
REFERENCE_FIELDS = (*HOUSEHOLD_FIELDS, *MEMBER_FIELDS, *HEALTH_FIELDS)

PROGRAM_FIELDS = {
    "4Ps": {
        "household": HOUSEHOLD_FIELDS[:2],
        "member": ("a07_age", "a05_sex", "a02_relation_to_hh_head"),
        "health": ("l07_currently_pregnant",), "minimum_age": 0,
    },
    "SLP": {
        "household": (),
        "member": ("a07_age", "b06_ofi", "e01_employed", "e18_class_of_worker",
                   "e11_nature_of_employment", "c07_tvet_graduate",
                   "c08_tvet_currently_attending"),
        "health": (), "minimum_age": 16,
    },
    "SPISC": {
        "household": ("l15_with_pwd_member",),
        "member": ("a07_age",), "health": ("l16_pwd",), "minimum_age": 0,
    },
    "TUPAD": {
        "household": (),
        "member": ("a07_age", "b06_ofi", "e01_employed",
                   "e11_nature_of_employment", "e18_class_of_worker"),
        "health": (), "minimum_age": 18,
    },
}

REASON_TEXT = {
    "child_age": "A child aged 0–18 was recorded in the household at the survey.",
    "senior_age": "A member aged 60 or older was recorded in the household at the survey.",
    "household_pregnancy": "The household reported a pregnant member at the time of the survey.",
    "member_pregnancy": (
        "The stored finding links this member to the household's pregnancy report. "
        "The member's pregnancy answer still needs confirmation."
    ),
    "EF": (
        "Recorded as not working during the week before the survey, supporting "
        "consideration for employment assistance."
    ),
    "MD": (
        "Recorded enterprise or family-business work supports consideration "
        "for livelihood assistance."
    ),
    "observed_nonwork": "Recorded as not working during the week before the survey.",
    "assumed_nonwork": (
        "No work answer was recorded. The stored finding treated this member as "
        "possibly not working; this was not a recorded No answer and must be confirmed."
    ),
    "irregular_work": "Short-term, seasonal, casual, or day-to-day work was recorded at the survey.",
    "assumed_overseas_work": (
        "Overseas status was recorded, but no work answer was available. "
        "Employment was not confirmed by a work response."
    ),
    "household_disability": (
        "The household reported a member with disability. This does not identify which member."
    ),
    "senior_disability": (
        "The stored finding links a disability response to this senior member. "
        "The response's meaning still needs confirmation."
    ),
}

WARNING_TEXT = {
    "pregnancy_routing_conflict": (
        "Household pregnancy answers conflict; pregnancy cannot be confirmed "
        "from those answers."
    ),
    "pregnancy_member_conflict": (
        "A member pregnancy answer could not be matched consistently with "
        "household information, age, or sex."
    ),
    "pregnancy_invalid_member_code": "A member pregnancy answer has no confirmed meaning.",
    "pregnancy_household_member_conflict": (
        "The household pregnancy report conflicts with the available member answers."
    ),
    "pregnancy_member_unresolved": (
        "The household reported pregnancy, but the pregnant member could not "
        "be identified reliably."
    ),
    "disability_household_member_conflict": (
        "The household disability report conflicts with the available member answers."
    ),
    "disability_member_conflict": (
        "A member disability answer could not be supported by a matching "
        "household report or member record."
    ),
    "disability_invalid_member_code": "A member disability answer has no confirmed meaning.",
    "senior_disability_unresolved": (
        "Available age or disability information does not confirm disability "
        "for this senior member."
    ),
    "employment_skip_conflict": (
        "Work answers are present where the overseas-status answer indicates "
        "that work questions were skipped."
    ),
    "nonwork_imputation_conflict": (
        "The work answer is missing but other job details are recorded. "
        "Whether the member worked remains unclear."
    ),
}

REQUIREMENT_TEXT = {
    "Official poverty-targeting classification.": (
        "Official program poverty assessment or targeting classification."
    ),
    "Current qualifying family composition.": (
        "Current family composition and qualifying children or pregnancy."
    ),
    "Existing beneficiary status and applicable program conditions.": (
        "Existing beneficiary status and other applicable program requirements."
    ),
    "Actual livelihood or employment assistance needs.": (
        "Current livelihood or employment assistance needs."
    ),
    "Participant suitability, preferences and official SLP assessment.": (
        "Participant preferences, suitability, and the official SLP assessment."
    ),
    "Written parental/legal guardian consent for a 16-17-year-old MD participant.": (
        "Written parent or legal guardian consent for a participant aged 16–17."
    ),
    "Current indigency and financial support.": (
        "Current financial circumstances and support received."
    ),
    "Existing pension receipt.": "Whether the senior currently receives a pension.",
    "Present-day circumstances and applicable SPISC conditions.": (
        "Current circumstances and applicable social pension requirements."
    ),
    "Current disadvantaged/displaced worker circumstances.": (
        "Current circumstances as a disadvantaged or displaced worker."
    ),
    "Current livelihood/employment situation, prior assistance and applicable DOLE conditions.": (
        "Current work or livelihood, previous assistance, and applicable DOLE requirements."
    ),
}
