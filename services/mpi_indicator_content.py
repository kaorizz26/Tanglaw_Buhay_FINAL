"""Presentation wording and source fields reviewed against the approved MPI rules.

This catalog describes stored outcomes; it never determines an indicator flag.
The scoring module remains authoritative for dimensions and exact weights.
"""

from services.vulnerability.health import FIES_FIELDS
from services.vulnerability.housing import ASSET_FIELDS


INDICATORS = {
    "School Attendance": {
        "member_fields": ("c03_currently_attending_school",),
        "ages": (5, 17),
        "rule": (
            "Any member aged 5–17 recorded as not attending school establishes deprivation. "
            "Reliably no applicable children establishes Not Deprived. Relevant unresolved "
            "information remains Unknown."
        ),
        "explanations": (
            (
                "The available information establishes no school-attendance deprivation "
                "among members aged 5–17; this can include a household with no children in "
                "that age range."
            ),
            (
                "At least one household member aged 5–17 was recorded as not currently "
                "attending school."
            ),
            (
                "Available age or school-attendance information could not establish the "
                "household's outcome."
            ),
        ),
    },
    "Educational Attainment": {
        "member_fields": ("c02_hgc_group",),
        "ages": (19, None),
        "rule": (
            "Any member aged 19+ who establishes secondary completion gives Not Deprived. "
            "Groups 7–17 count as complete; group 5 uses the approved age-25 approximation. "
            "Groups 1–4 and 6 do not establish completion. Reliably no members aged 19+ "
            "gives Deprived; relevant gaps remain Unknown."
        ),
        "explanations": (
            (
                "At least one member aged 19 or older establishes secondary completion under "
                "the project's education rule."
            ),
            (
                "No member aged 19 or older establishes secondary completion under the "
                "project rule; households reliably without such adults also receive this "
                "result."
            ),
            (
                "Available age or educational-attainment information could not establish "
                "secondary completion or its reliable absence."
            ),
        ),
        "limitations": (
            "Group 5 uses an age-25 cohort approximation, not proof of a recorded graduation "
            "and not an official PSA rule.",
        ),
    },
    "Food Insecurity": {
        "household_fields": FIES_FIELDS,
        "rule": (
            "Four or more confirmed Yes responses among J01–J08 establish deprivation. Fewer "
            "than four possible Yes responses establishes Not Deprived. If unresolved "
            "answers can cross the cutoff, the result remains Unknown. J09 is excluded."
        ),
        "explanations": (
            (
                "The eight food-access responses establish fewer than four possible Yes "
                "answers under the project's food-insecurity rule."
            ),
            (
                "At least four food-access difficulties were reported across the eight "
                "questions used by this assessment."
            ),
            (
                "Missing or unresolved food-access answers could change whether the "
                "project's four-answer cutoff is reached."
            ),
        ),
        "limitations": (
            "The four-answer cutoff is a project pilot approximation, not an official "
            "calibrated FIES measurement.",
        ),
    },
    "Illness/Treatment Proxy": {
        "household_fields": ("l33_member_who_has_been_sick",),
        "health_fields": ("l34_has_been_sick", "l38_availed_medical_treatment"),
        "rule": (
            "L33=2 establishes Not Deprived and skips illness follow-up. For L33=1, an "
            "affected member with L38=2 establishes deprivation. Not Deprived otherwise "
            "requires an identified affected member, complete consistent joined evidence and "
            "treatment for all affected members. Other relevant gaps or conflicts remain "
            "Unknown. L35 does not establish medical need or bypass L38."
        ),
        "explanations": (
            (
                "The stored outcome establishes either no reported household illness, or "
                "complete consistent evidence that affected members received treatment."
            ),
            (
                "At least one affected member was recorded as not having obtained medical "
                "treatment under the project's illness interpretation."
            ),
            (
                "Available illness and linked member-treatment information could not "
                "establish a complete, consistent household outcome."
            ),
        ),
        "limitations": (
            "L34 uses the project's provisional 1=affected, 2=unaffected interpretation. The "
            "export recoding is not independently PSA-confirmed. This proxy does not infer "
            "medical necessity.",
        ),
    },
    "Sanitation": {
        "household_fields": ("q14_toilet_facility", "q19_share_toilet"),
        "rule": (
            "Combine toilet type and sharing using the approved mappings. Any known deprived "
            "component establishes deprivation. Not Deprived requires both mapped components "
            "to be known and not deprived; other combinations remain Unknown."
        ),
        "explanations": (
            (
                "Both the recorded toilet facility and sharing arrangement meet the "
                "project's non-deprivation rules."
            ),
            (
                "The toilet facility or its sharing arrangement meets at least one approved "
                "sanitation deprivation criterion."
            ),
            "The combined toilet and sharing information could not establish a sanitation outcome.",
        ),
    },
    "Water": {
        "household_fields": ("q01_main_water",),
        "rule": (
            "Use the approved water-source code mapping; missing, invalid or unmapped "
            "answers remain Unknown."
        ),
        "explanations": (
            (
                "The recorded main water source maps to Not Deprived under the approved "
                "project reference."
            ),
            "The recorded main water source maps to Deprived under the approved project reference.",
            (
                "The available water-source response could not be assigned a determined "
                "outcome under the approved mapping."
            ),
        ),
    },
    "Assets": {
        "household_fields": ASSET_FIELDS,
        "rule": (
            "Positive recorded quantities mean ownership. A known mobility asset, or both a "
            "communication and durable asset, establishes Not Deprived. Definitely no "
            "mobility asset and definitely no owned item in either the communication or "
            "durable group establishes Deprived. Remaining unresolved combinations stay "
            "Unknown."
        ),
        "explanations": (
            (
                "Recorded ownership establishes a qualifying mobility asset or both "
                "communication and durable assets under the project rule."
            ),
            (
                "The stored outcome establishes no mobility asset and no owned item in at "
                "least one of the other required asset groups."
            ),
            (
                "Available asset quantities could not establish the required ownership "
                "combination or its reliable absence."
            ),
        ),
        "limitations": (
            "Jeep and tricycle are approved pilot mobility crosswalks; they are not exact "
            "official category equivalents.",
        ),
    },
    "Tenure": {
        "household_fields": ("r10_tenure",),
        "rule": (
            "Use the approved house-and-lot tenure mapping; an unresolved ownership "
            "arrangement remains Unknown."
        ),
        "explanations": (
            (
                "The recorded house-and-lot tenure maps to Not Deprived under the approved "
                "project reference."
            ),
            (
                "The recorded house-and-lot tenure maps to Deprived under the approved "
                "project reference."
            ),
            (
                "The available tenure response could not establish an outcome under the "
                "approved mapping."
            ),
        ),
    },
    "Housing Materials": {
        "household_fields": ("r03_roof", "r04_outer_walls", "r06_floor"),
        "rule": (
            "Combine roof, outer-wall and floor mappings. Any known deprived material "
            "establishes deprivation. Not Deprived requires all three known non-deprived "
            "components; remaining gaps stay Unknown."
        ),
        "explanations": (
            (
                "All three recorded roof, wall and floor components meet the project's "
                "non-deprivation rules."
            ),
            (
                "At least one recorded roof, outer-wall or floor component meets an approved "
                "housing-material deprivation criterion."
            ),
            (
                "The combined roof, outer-wall and floor information could not establish the "
                "household's outcome."
            ),
        ),
    },
    "Electricity": {
        "household_fields": ("r12_electricity",),
        "rule": (
            "Use the approved electricity-availability mapping; missing, invalid or unmapped "
            "answers remain Unknown."
        ),
        "explanations": (
            (
                "The recorded electricity response maps to Not Deprived under the approved "
                "project reference."
            ),
            (
                "The recorded electricity response maps to Deprived under the approved "
                "project reference."
            ),
            (
                "The available electricity response could not establish an outcome under the "
                "approved mapping."
            ),
        ),
    },
    "Internet": {
        "household_fields": ("n01_internet_access",),
        "rule": (
            "Use the approved internet-access mapping and the original n01_internet_access "
            "source field."
        ),
        "explanations": (
            (
                "The recorded internet-access response maps to Not Deprived under the "
                "approved project reference."
            ),
            (
                "The recorded internet-access response maps to Deprived under the approved "
                "project reference."
            ),
            (
                "The available internet-access response could not establish an outcome under "
                "the approved mapping."
            ),
        ),
    },
    "Overcrowding": {
        "household_fields": ("r07_floor_area",),
        "roster_count": True,
        "rule": (
            "Recorded floor area below four square metres per roster member establishes "
            "deprivation. Exactly four square metres per member is Not Deprived. All roster "
            "members count; invalid area or absent/invalid counts remain Unknown."
        ),
        "explanations": (
            (
                "The recorded floor area meets the project's minimum of four square metres "
                "per household roster member."
            ),
            "The recorded floor area is below four square metres per household roster member.",
            (
                "Available floor-area or roster information could not establish the space "
                "available per household member."
            ),
        ),
    },
    "Public Transportation": {
        "household_fields": ("h01_access_to_public_transportation",),
        "rule": (
            "Use the approved public-transportation-access mapping; missing, invalid or "
            "unmapped answers remain Unknown."
        ),
        "explanations": (
            (
                "The recorded transportation-access response maps to Not Deprived under the "
                "approved project reference."
            ),
            (
                "The recorded transportation-access response maps to Deprived under the "
                "approved project reference."
            ),
            (
                "The available transportation-access response could not establish an outcome "
                "under the approved mapping."
            ),
        ),
    },
    "Employment Proxy": {
        "member_fields": ("b06_ofi", "e01_employed"),
        "ages": (18, 65),
        "rule": (
            "Deprived means strictly more than half of applicable members aged 18–65 are "
            "classified as not working. Exactly half or reliably no applicable members gives "
            "Not Deprived. Unknown answers and applicability remain within the uncertainty "
            "bounds; they are never dropped to simplify the denominator."
        ),
        "explanations": (
            (
                "The stored assessment establishes that the more-than-half nonworking "
                "criterion is not met; this can include reliably no applicable adults."
            ),
            (
                "More than half of the applicable adults aged 18–65 are classified as not "
                "working under the project rule."
            ),
            (
                "Available age, work-status or applicability information could not establish "
                "the majority-based household outcome."
            ),
        ),
        "limitations": (
            "For ages 18–65, B06=1/2/3 with missing E01 is an approved assumed-working proxy "
            "for this indicator only. Populated E01 in those skipped categories is "
            "conflicting evidence. B06=6 remains excluded. This is not official labor-force "
            "unemployment.",
        ),
    },
    "Underemployment Proxy": {
        "member_fields": ("b06_ofi", "e01_employed", "e15_want_more_hours"),
        "ages": (18, 65),
        "rule": (
            "Deprived means strictly more than half of applicable employed members aged "
            "18–65 want more hours (E15=1). Exactly half or reliably no applicable employed "
            "members gives Not Deprived. Unresolved work and answer gaps remain bounded, not "
            "silently excluded."
        ),
        "explanations": (
            (
                "The stored assessment establishes that the more-than-half additional-hours "
                "criterion is not met; this can include no applicable employed adults."
            ),
            (
                "More than half of the applicable employed adults aged 18–65 meet the "
                "project's additional-hours criterion."
            ),
            (
                "Available employment, applicability or additional-hours information could "
                "not establish the household's outcome."
            ),
        ),
        "limitations": (
            "E15 is documented Synthetic Support in the pilot, not wholly observed CBMS. The "
            "Employment Proxy's assumed-working treatment does not supply E15 or add OFWs to "
            "this denominator.",
        ),
    },
    "Working Child Not in School": {
        "member_fields": ("b06_ofi", "e01_employed", "c03_currently_attending_school"),
        "ages": (5, 17),
        "rule": (
            "Any applicable member aged 5–17 with recorded work (E01=1) and nonattendance "
            "(C03=2) establishes deprivation. Known nonwork or attendance excludes a member "
            "despite other gaps. For ages 5–14, B06 does not gate Section E; at age 15+, "
            "B06=4/5/7 permits it and 1/2/3/6 skips it. Relevant unresolved cases remain "
            "Unknown."
        ),
        "explanations": (
            (
                "The available information establishes no applicable child aged 5–17 meeting "
                "both the work and nonattendance criteria; there may be no children in that "
                "range."
            ),
            (
                "At least one applicable child aged 5–17 was recorded as working and not "
                "currently attending school."
            ),
            (
                "Available age, work, school-attendance or applicability information could "
                "not rule the household's condition in or out."
            ),
        ),
        "limitations": (
            "Applicable missing E01 remains unknown. B06 is outside its questionnaire age "
            "scope below 15; no resident code is invented. The adult assumed-working proxy "
            "does not prove a working child.",
        ),
    },
}

IDENTITY_FIELDS = ("a01_first_name", "a01_middle_name", "a01_last_name", "a07_age")
HOUSEHOLD_FIELDS = tuple(dict.fromkeys(
    field for spec in INDICATORS.values() for field in spec.get("household_fields", ())
))
MEMBER_FIELDS = tuple(dict.fromkeys((*IDENTITY_FIELDS, *(
    field for spec in INDICATORS.values() for field in spec.get("member_fields", ())
))))
HEALTH_FIELDS = ("l34_has_been_sick", "l38_availed_medical_treatment")
REFERENCE_FIELDS = tuple(dict.fromkeys((*HOUSEHOLD_FIELDS, *MEMBER_FIELDS, *HEALTH_FIELDS)))
