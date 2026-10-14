"""Arrange existing decoded MPI evidence for a concise staff display."""

from decimal import Decimal, InvalidOperation

from services.vulnerability.housing import (
    COMMUNICATION_ASSETS,
    DURABLE_ASSETS,
    MOBILITY_ASSETS,
)


FOOD_LABELS = {
    "j01_worried": "Worried about enough food",
    "j02_not_eaten_healthy": "Could not eat healthy food",
    "j03_ate_few_food": "Ate only a few kinds of food",
    "j04_skipped_meal": "Skipped a meal",
    "j05_ate_less": "Ate less than needed",
    "j06_ran_out_of_food": "Ran out of food",
    "j07_hungry": "Was hungry but did not eat",
    "j08_not_eaten_whole_day": "Went a whole day without eating",
}

FIELD_LABELS = {
    **FOOD_LABELS,
    "q01_main_water": "Main water source",
    "q14_toilet_facility": "Toilet facility",
    "q19_share_toilet": "Shared toilet",
    "r03_roof": "Roof",
    "r04_outer_walls": "Outer walls",
    "r06_floor": "Floor",
    "r07_floor_area": "Floor area",
    "r10_tenure": "House and lot tenure",
    "r12_electricity": "Electricity",
    "n01_internet_access": "Internet access",
    "h01_access_to_public_transportation": "Public transportation access",
    "l33_member_who_has_been_sick": "Household illness reported",
    "l34_has_been_sick": "Member affected by illness",
    "l38_availed_medical_treatment": "Medical treatment received",
    "c02_hgc_group": "Educational attainment",
    "c03_currently_attending_school": "Currently attending school",
    "b06_ofi": "Overseas Filipino status",
    "e01_employed": "Working",
    "e15_want_more_hours": "Wants more work hours",
}

CAUTIONS = {
    "Educational Attainment": "One education category uses an age-based estimate of completion.",
    "Food Insecurity": "This is a project screening measure, not an official food-insecurity classification.",
    "Underemployment Proxy": "The additional-hours information includes data supplied for this pilot; it was not fully recorded in the original CBMS survey.",
}

DIRECT_CONDITIONS = {
    "Water": "Main water source",
    "Tenure": "House and lot tenure",
    "Electricity": "Electricity response",
    "Internet": "Internet access",
    "Public Transportation": "Public transportation access",
}

STAFF_EXPLANATIONS = {
    "School Attendance": (
        "No school-age member was found to be out of school.",
        "A school-age household member was recorded as not attending school.",
        "School attendance could not be confirmed for all relevant members.",
    ),
    "Educational Attainment": (
        "At least one adult meets the assessment's secondary-education criterion.",
        "No adult meets the assessment's secondary-education criterion.",
        "Adult education information is incomplete or unclear.",
    ),
    "Food Insecurity": (
        "The recorded food-access difficulties do not meet the assessment cutoff.",
        "At least four food-access difficulties were reported.",
        "Some food-access answers could change this result.",
    ),
    "Illness/Treatment Proxy": (
        "No untreated illness was established from the available responses.",
        "An affected member was recorded as not receiving medical treatment.",
        "Illness or treatment responses do not establish a clear result.",
    ),
    "Sanitation": (
        "The recorded toilet type and sharing arrangement meet the assessment criteria.",
        "The recorded toilet type or sharing arrangement indicates a sanitation concern.",
        "Toilet type or sharing information is incomplete or unclear.",
    ),
    "Water": (
        "The recorded water source meets the assessment criterion.",
        "The recorded water source indicates a water-access concern.",
        "The household's main water source could not be confirmed.",
    ),
    "Assets": (
        "Recorded assets meet the assessment's household ownership criterion.",
        "Recorded assets do not meet the assessment's household ownership criterion.",
        "Asset quantities do not establish a clear result.",
    ),
    "Tenure": (
        "The recorded housing tenure meets the assessment criterion.",
        "The recorded housing tenure indicates a concern.",
        "The housing tenure response could not be confirmed.",
    ),
    "Housing Materials": (
        "The recorded roof, walls, and floor meet the assessment criteria.",
        "At least one recorded housing material indicates a concern.",
        "Roof, wall, or floor information is incomplete or unclear.",
    ),
    "Electricity": (
        "The recorded electricity response meets the assessment criterion.",
        "The recorded electricity response indicates a concern.",
        "Electricity access could not be confirmed.",
    ),
    "Internet": (
        "Internet access was recorded for this household.",
        "Internet access does not meet the assessment criterion.",
        "The internet-access response could not be confirmed.",
    ),
    "Overcrowding": (
        "Recorded floor space meets the assessment's minimum per member.",
        "Recorded floor space is below the assessment's minimum per member.",
        "Floor space or household size is incomplete or unclear.",
    ),
    "Public Transportation": (
        "The recorded transport-access response meets the assessment criterion.",
        "The recorded transport-access response indicates a concern.",
        "Public transportation access could not be confirmed.",
    ),
    "Employment Proxy": (
        "The assessed share of adults not working does not exceed half.",
        "More than half of applicable adults are classified as not working.",
        "Adult work information does not establish a clear household result.",
    ),
    "Underemployment Proxy": (
        "The assessed share wanting more work hours does not exceed half.",
        "More than half of applicable working adults meet the additional-hours criterion.",
        "Work-hours information does not establish a clear household result.",
    ),
    "Working Child Not in School": (
        "No applicable child was found to be both working and out of school.",
        "A child was recorded as both working and not attending school.",
        "Work or school responses do not establish a clear result for children.",
    ),
}


def _display_value(cell):
    """Use verified decoded text; never present an unresolved code as a known answer."""
    meaning = cell["meaning"]
    if meaning.startswith("Not recorded or missing"):
        return "Not recorded"
    if "needs verification" in meaning.lower() or "no verified code meaning" in meaning.lower():
        return "Meaning needs verification"
    if cell["variable"] == "r07_floor_area":
        return f"{cell['raw']} m²" if meaning.startswith("Recorded floor area") else meaning
    if cell["variable"].startswith("r16_"):
        return f"{cell['raw']} recorded" if meaning.startswith("Recorded number") else meaning
    if cell["variable"] == "b06_ofi" and meaning == "No, Resident (Philippines)":
        return "Resident in the Philippines"
    if cell["variable"] == "l34_has_been_sick" and "provisional project interpretation" in meaning:
        return "Reported affected by illness; this answer needs verification"
    return meaning.rstrip(".")


def _display_cell(cell):
    return {
        "label": FIELD_LABELS.get(cell["variable"], cell["label"]),
        "value": _display_value(cell),
        "source": cell,
    }


def _household_groups(name, cells):
    if name == "Assets":
        groups = (
            ("Communication", COMMUNICATION_ASSETS),
            ("Durable household items", DURABLE_ASSETS),
            ("Transportation", MOBILITY_ASSETS),
        )
        return [
            {"title": title, "layout": "grid", "cells": [
                _display_cell(cell) for cell in cells if cell["variable"] in fields
            ]}
            for title, fields in groups
        ]
    return [{
        "title": "Food access responses" if name == "Food Insecurity" else "Household responses",
        "layout": "grid" if name == "Food Insecurity" else "rows",
        "cells": [_display_cell(cell) for cell in cells],
    }] if cells else []


def _member_cautions(notes):
    cautions = []
    for note in notes:
        if "assumed-working" in note:
            cautions.append("Work status was assumed for this indicator; no direct work answer was recorded.")
        elif "routing conflict" in note or "conflicting follow-up" in note:
            cautions.append("Recorded answers conflict and need staff verification.")
        elif "No E01 work response" in note:
            cautions.append("No direct work answer was recorded.")
        elif "outside its questionnaire age scope" in note:
            cautions.append("The activity question did not apply at this recorded age.")
    return list(dict.fromkeys(cautions))


def _space_comparison(evidence):
    """Display space per roster member only when both recorded inputs are valid."""
    count = evidence["roster_count"]
    cells = evidence["household_cells"]
    if count is None or count <= 0 or not cells:
        return None
    try:
        area = Decimal(cells[0]["raw"])
    except InvalidOperation:
        return None
    if not area.is_finite() or area < 0:
        return None
    return (
        f"{area / count:.2f} m² per person from recorded area and roster size; "
        "assessment benchmark: 4 m² per person"
    )


def _indicator_caution(name, evidence):
    """Surface only material caveats relevant to this household's displayed answers."""
    if name == "Assets":
        for cell in evidence["household_cells"]:
            if cell["variable"] not in ("r16_s_jeep", "r16_v_tricycle"):
                continue
            try:
                if Decimal(cell["raw"]) > 0:
                    return "Some vehicle categories use project interpretations of the survey answers."
            except InvalidOperation:
                continue
    if name == "Employment Proxy" and any(
        "assumed-working" in note
        for member in evidence["members"] for note in member["notes"]
    ):
        return "Some adult work classifications use an assumption, not a direct work answer."
    if name == "Illness/Treatment Proxy" and evidence["members"]:
        return "The source export's illness response has a provisional interpretation."
    if name == "Educational Attainment" and any(
        cell["variable"] == "c02_hgc_group" and cell["raw"] == "5"
        for member in evidence["members"] for cell in member["cells"]
    ):
        return CAUTIONS[name]
    return CAUTIONS.get(name) if name in ("Food Insecurity", "Underemployment Proxy") else None


def prepare_evidence_view(name, evidence):
    """Group decoded answers without changing the source cells or stored outcome."""
    members = []
    for member in evidence["members"]:
        members.append({
            **member,
            "display_cells": [
                _display_cell(cell) for cell in member["cells"]
                if cell["variable"] != "a07_age"
            ],
            "cautions": _member_cautions(member["notes"]),
        })
    return {
        **evidence,
        "groups": _household_groups(name, evidence["household_cells"]),
        "members": members,
        "caution": _indicator_caution(name, evidence),
        "space_comparison": _space_comparison(evidence) if name == "Overcrowding" else None,
    }


def direct_condition(name, flag, evidence):
    """Describe one recorded answer only when its decoded meaning is verified."""
    if name not in DIRECT_CONDITIONS or flag is None or not evidence["household_cells"]:
        return None
    value = _display_value(evidence["household_cells"][0])
    if value in ("Not recorded", "Meaning needs verification"):
        return None
    return f"{DIRECT_CONDITIONS[name]} recorded: {value}."
