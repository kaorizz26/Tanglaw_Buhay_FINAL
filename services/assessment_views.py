"""Read-only presentation of engine outputs and their original source inputs."""

from services.data_loader import join_section_l
from services.subsidy_screening import PROGRAMS
from services.vulnerability.direct import _code_key
from services.vulnerability.health import FIES_FIELDS
from services.vulnerability.housing import ASSET_FIELDS
from services.vulnerability.scoring import DIMENSIONS


INDICATOR_CONTEXT = {
    "School Attendance": ("Any recorded member aged 5–17 not attending school establishes deprivation. Relevant missing evidence stays unknown.", (), ("a07_age", "c03_currently_attending_school")),
    "Educational Attainment": ("At least one secondary-complete member aged 19+ establishes non-deprivation. HGC group 5 uses the approved age-25 cohort approximation; no eligible adult gives deprivation under this pilot rule.", (), ("a07_age", "c02_hgc_group")),
    "Food Insecurity": ("Four confirmed Yes answers across J01–J08 establish deprivation. Unknown responses remain unresolved if they could cross that cutoff. This is a pilot raw-count approximation, not calibrated FIES.", FIES_FIELDS, ()),
    "Illness/Treatment Proxy": ("L33=2 establishes non-deprivation. With illness reported, an affected member without treatment establishes deprivation. Zero requires complete, consistent evidence of treatment. L34 Yes/No semantics are provisional; medical necessity is not inferred.", ("l33_member_who_has_been_sick",), ("section_l_line_number", "l34_has_been_sick", "l38_availed_medical_treatment")),
    "Sanitation": ("Mapped toilet type and sharing responses are combined. Any confirmed deprived component establishes deprivation; zero requires both known non-deprived components.", ("q14_toilet_facility", "q19_share_toilet"), ()),
    "Water": ("The original main-water-source code is classified using the finalized MPI mapping. Unmapped or invalid responses stay unknown.", ("q01_main_water",), ()),
    "Assets": ("Known mobility ownership, or both communication and durable ownership, establishes non-deprivation. No mobility and no owned item in either of the other groups establishes deprivation. Only the approved 15 asset fields are used.", ASSET_FIELDS, ()),
    "Tenure": ("The recorded tenure response is classified using the finalized MPI mapping; unresolved ownership information is not treated as a negative.", ("r10_tenure",), ()),
    "Housing Materials": ("Any mapped deprivation in roof, walls or floor establishes deprivation. Non-deprivation requires all three components to be known zeros.", ("r03_roof", "r04_outer_walls", "r06_floor"), ()),
    "Electricity": ("Recorded electricity access is classified by the finalized MPI mapping. Missing or unmapped answers remain unknown.", ("r12_electricity",), ()),
    "Internet": ("Recorded internet access is classified by the finalized MPI mapping. The export column spelling is preserved.", ("n01_internet_acess",), ()),
    "Overcrowding": ("A recorded floor area below four square metres per roster member establishes deprivation. Invalid area or an unreliable member count stays unknown.", ("r07_floor_area",), ("line_number",)),
    "Public Transportation": ("The recorded access response is classified by the finalized MPI mapping; no present-day transport condition is inferred.", ("h01_access_to_public_transportation",), ()),
    "Employment Proxy": ("Strictly more than half of applicable adults aged 18–65 classified not working establishes deprivation. Applicable missing E01 and adult B06 1/2/3 skips use the approved, labeled pilot assumptions. Uncertain applicability is retained.", (), ("a07_age", "b06_ofi", "e01_employed")),
    "Underemployment Proxy": ("Strictly more than half of applicable employed adults aged 18–65 wanting more hours establishes deprivation. E15 is Synthetic Support, not an observed CBMS response; the OFW assumption never supplies E15.", (), ("a07_age", "b06_ofi", "e01_employed", "e15_want_more_hours")),
    "Working Child Not in School": ("Any applicable member aged 5–17 with confirmed work and C03=2 establishes deprivation. The adult OFW assumption does not establish child work.", (), ("a07_age", "b06_ofi", "e01_employed", "c03_currently_attending_school")),
}
HOUSING_FIELDS = ("r01_building_type", "r10_tenure", "r07_floor_area", "r09_number_of_bedrooms", "r03_roof", "r04_outer_walls", "r06_floor", "q01_main_water", "q14_toilet_facility", "q19_share_toilet", "r12_electricity", "n01_internet_acess", "h01_access_to_public_transportation")
EDUCATION_FIELDS = ("c02_hgc_group", "c03_currently_attending_school", "c07_tvet_graduate", "c08_tvet_currently_attending")
EMPLOYMENT_FIELDS = ("b06_ofi", "e01_employed", "e11_nature_of_employment", "e18_class_of_worker", "e15_want_more_hours")
HEALTH_FIELDS = ("l01_with_member_who_has_been_pregnant", "l06_with_member_currently_pregnant", "l15_with_pwd_member", "l33_member_who_has_been_sick")


def reference_lookup(references):
    """Index finalized labels only; this lookup never assigns deprivation flags."""
    definitions = {row["variable_name"]: row for row in references["definitions"].to_dict("records")}
    codes = {}
    for row in references["codebook"].to_dict("records"):
        codes[(row["variable_name"], _code_key(row["code"]))] = row["label"]
    return {"definitions": definitions, "codes": codes, "coded_fields": {field for field, _ in codes}}


def display_field(field, value, lookup):
    """Show decoded meaning and original code separately, never filling gaps."""
    definition = lookup["definitions"].get(field, {})
    raw = "" if value is None else str(value)
    missing = _code_key(value) is None or raw.strip().upper() in ("", "NA")
    decoded = lookup["codes"].get((field, _code_key(value)))
    if missing:
        display = "Not recorded"
    elif decoded is not None:
        display = decoded
    elif field in lookup["coded_fields"]:
        display = "Unmapped response"
    else:
        display = raw
    return {
        "variable": field, "label": definition.get("display_label", field),
        "value": display, "raw": raw or "Blank", "decoded": decoded is not None,
        "provenance": definition.get("provenance", ""),
        "pilot": field in ("l07_currently_pregnant", "l16_pwd", "l34_has_been_sick"),
    }


def field_values(row, fields, lookup):
    return [display_field(field, row.get(field), lookup) for field in fields]


def member_name(member):
    parts = [member.get(field, "") for field in ("a01_first_name", "a01_middle_name", "a01_last_name", "a01_suffix")]
    name = " ".join(str(value).strip() for value in parts if _code_key(value) is not None and str(value).strip().upper() not in ("", "NA"))
    return name or "Name not recorded"


def build_profiles(datasets, lookup):
    """Derive the head only from one unambiguous roster relationship code 1."""
    joined = join_section_l(datasets["section_a_e_indiv"], datasets["section_l02_to_l41"])
    groups = {}
    for row in joined.to_dict("records"):
        row["display_name"] = member_name(row)
        groups.setdefault(row["uuid"], []).append(row)
    profiles = {}
    for household in datasets["household"].to_dict("records"):
        uuid = household["uuid"]
        members = groups.get(uuid, [])
        heads = [row for row in members if _code_key(row.get("a02_relation_to_hh_head")) == "1"]
        profiles[uuid] = {
            "uuid": uuid, "head": member_name(heads[0]) if len(heads) == 1 else "Head not reliably identified",
            "barangay": display_field("barangay", household.get("barangay"), lookup)["value"],
            "member_count": len(members), "household": household, "members": members,
            "housing": field_values(household, HOUSING_FIELDS, lookup),
            "health": field_values(household, HEALTH_FIELDS, lookup),
            "food": field_values(household, FIES_FIELDS, lookup),
        }
    return profiles


def assessment_details(result, profile, lookup):
    """Attach explanations and source context; display the engine's flags unchanged."""
    dimensions = []
    for name, (weight, indicators) in DIMENSIONS.items():
        outcomes = []
        for indicator in indicators:
            explanation, household_fields, member_fields = INDICATOR_CONTEXT[indicator]
            context = []
            if household_fields:
                context.append({"scope": "Household", "fields": field_values(profile["household"], household_fields, lookup)})
            if indicator == "Overcrowding":
                context.append({"scope": "Roster", "fields": [{"label": "Recorded roster members", "value": profile["member_count"], "raw": str(profile["member_count"]), "variable": "uuid + line_number", "decoded": False, "pilot": False, "provenance": "Derived from the supplied roster"}]})
            elif member_fields:
                for member in profile["members"]:
                    context.append({"scope": f"Member {member['line_number']} · {member['display_name']}", "fields": field_values(member, member_fields, lookup)})
            flag = result["mpi"][indicator]
            outcomes.append({"name": indicator, "flag": flag,
                             "label": "Insufficient Data" if flag is None else ("Deprived" if flag == 1 else "Not Deprived"),
                             "explanation": explanation, "weight_pct": float(weight * 100), "evidence": context})
        dimensions.append({"name": name, "indicators": outcomes,
                           "deprived": sum(item["flag"] == 1 for item in outcomes),
                           "unknown": sum(item["flag"] is None for item in outcomes)})
    programs = []
    for code in PROGRAMS:
        original = result["programs"][code]
        evidence = []
        for item in original["supporting_evidence"]:
            context = {"item": item, "fields": field_values(item["observed_values"], tuple(item["observed_values"]), lookup)}
            context["corroboration"] = field_values(item.get("corroborating_household_values", {}), tuple(item.get("corroborating_household_values", {})), lookup)
            evidence.append(context)
        programs.append({"code": code, "result": original, "evidence": evidence})
    return {"dimensions": dimensions, "programs": programs}


def listing_rows(workspace, *, query="", barangay="", program="", quality="", sort="rank"):
    """Filter precomputed outputs; their full-batch rank is never recomputed."""
    rows = []
    for uuid, profile in workspace["profiles"].items():
        result = workspace["results"][uuid]
        if query and query.casefold() not in " ".join((str(uuid), profile["head"], profile["barangay"])).casefold():
            continue
        if barangay and profile["barangay"] != barangay:
            continue
        if program and result["programs"][program]["screening_status"] != "For Review":
            continue
        if quality == "complete" and result["mpi"]["unknown_indicator_count"]:
            continue
        if quality == "needs_review" and not result["mpi"]["unknown_indicator_count"]:
            continue
        rows.append({"profile": profile, "result": result})
    sorting = {
        "rank": lambda row: (-row["result"]["mpi"]["vulnerability_score"], row["profile"]["uuid"]),
        "score_asc": lambda row: (row["result"]["mpi"]["vulnerability_score"], row["profile"]["uuid"]),
        "coverage": lambda row: (row["result"]["mpi"]["coverage_pct"], row["profile"]["uuid"]),
        "id": lambda row: row["profile"]["uuid"],
    }
    return sorted(rows, key=sorting[sort])
