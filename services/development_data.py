"""Synthetic assessment and CSV test inputs; never reads private source files or a database."""

import json
from pathlib import Path

import pandas as pd

from services.vulnerability.health import FIES_FIELDS
from services.vulnerability.housing import ASSET_FIELDS


FIXTURE_METADATA = Path(__file__).resolve().parents[1] / "fixtures" / "development_metadata.json"


def development_sources():
    """Build sample records; calculated flags, scores and ranks are never stored."""
    households, members, health = [], [], []
    barangays = ("Sample Barangay North", "Sample Barangay Central", "Sample Barangay East", "Sample Barangay South")
    for number in range(1, 33):
        uuid = f"DEV-{number:04d}"
        household = {
            "uuid": uuid, "barangay": barangays[(number - 1) % 4], "city_mun": "Pozorrubio",
            "q01_main_water": 3 if number % 3 == 0 else 1,
            "q14_toilet_facility": 23 if number % 4 == 0 else 12,
            "q19_share_toilet": 1 if number % 5 == 0 else 2,
            "r01_building_type": 1, "r03_roof": 5 if number % 6 == 0 else 1,
            "r04_outer_walls": 2 if number % 3 == 0 else 1, "r06_floor": 5 if number % 7 == 0 else 1,
            "r07_floor_area": 10 if number % 5 == 0 else 48 + number,
            "r09_number_of_bedrooms": 2, "r10_tenure": 4 if number % 8 == 0 else 1,
            "r12_electricity": 2 if number % 11 == 0 else 1,
            "n01_internet_access": 2 if number % 2 == 0 else 1,
            "h01_access_to_public_transportation": 2 if number % 4 == 0 else 1,
            "l01_with_member_who_has_been_pregnant": 1 if number % 9 == 0 else 2,
            "l06_with_member_currently_pregnant": 1 if number % 9 == 0 else 2,
            "l15_with_pwd_member": 1 if number % 10 == 0 else 2,
            "l33_member_who_has_been_sick": 1 if number % 6 == 0 else 2,
        }
        household.update(dict.fromkeys(ASSET_FIELDS, 0))
        if number % 3:
            household["r16_n_mobile_phone"] = 1
            household["r16_a_refrigerator"] = 1
        for index, field in enumerate(FIES_FIELDS):
            household[field] = 1 if index < number % 8 else 2
        # Deliberate gaps/contradictions exercise unknowns through the real engines.
        if number % 13 == 0:
            household["q19_share_toilet"] = "NA"
        if number == 29:
            household["l33_member_who_has_been_sick"] = "NA"
            household.update({field: "NA" for field in FIES_FIELDS[3:]})
        if number == 30:
            household["l06_with_member_currently_pregnant"] = 1
            household["l01_with_member_who_has_been_pregnant"] = 2
        households.append(household)

        ages = [27 + number % 25, 25 + number % 23]
        if number % 5 != 0:
            ages.append(6 + number % 12)
        if number % 4 == 0:
            ages.append(60 + number % 19)
        for line, age in enumerate(ages, 1):
            adult = age >= 18
            working = adult and (number + line) % 3 != 0
            row = {
                "uuid": uuid, "line_number": line, "barangay": household["barangay"], "city_mun": "Pozorrubio",
                "a01_first_name": "Sample" if line == 1 else "Member",
                "a01_last_name": f"Household {number:02d}" if line == 1 else f"{line:02d}",
                "a01_middle_name": "", "a01_suffix": "", "a02_relation_to_hh_head": 1 if line == 1 else (2 if line == 2 else (5 if age >= 60 else 3)),
                "a05_sex": 2 if line == 2 else 1, "a07_age": age, "a09_marital_status": 2 if adult else 1,
                "b06_ofi": 7, "c02_hgc_group": 7 if adult and number % 4 else (4 if adult else 3),
                "c03_currently_attending_school": 1 if not adult else "NA",
                "c07_tvet_graduate": 1 if adult and number % 7 == 0 else 2, "c08_tvet_currently_attending": 2,
                "e01_employed": 1 if working else 2,
                "e11_nature_of_employment": (2 if number % 2 == 0 else 1) if working else "NA",
                "e15_want_more_hours": (1 if number % 3 == 1 else 2) if working else "NA",
                "e18_class_of_worker": (3 if number % 4 == 0 else 1) if working else "NA",
            }
            if not adult and number % 7 == 0:
                row.update(e01_employed=1, e11_nature_of_employment=2, e15_want_more_hours=2,
                           e18_class_of_worker=6, c03_currently_attending_school=2)
            if adult and number % 8 == 0:
                row.update(b06_ofi=1, e01_employed="NA", e11_nature_of_employment="NA",
                           e18_class_of_worker="NA", e15_want_more_hours="NA")
            if number == 31 and line == 1:
                row.update(e01_employed="NA", e11_nature_of_employment="NA",
                           e18_class_of_worker="NA", e15_want_more_hours="NA")
            if number == 32 and line == 3:
                row["a07_age"] = "NA"
            members.append(row)
            affected = household["l33_member_who_has_been_sick"] == 1 and line == 1
            health.append({
                "uuid": uuid, "section_l_line_number": line,
                "l07_currently_pregnant": 1 if number % 9 == 0 and line == 2 else 2,
                "l16_pwd": 1 if number % 10 == 0 and line == 1 else 2,
                "l34_has_been_sick": 1 if affected else 2,
                "l38_availed_medical_treatment": (2 if number % 12 == 0 else 1) if affected else "NA",
            })

    datasets = {
        "household": pd.DataFrame(households, dtype=object),
        "section_a_e_indiv": pd.DataFrame(members, dtype=object),
        "section_l02_to_l41": pd.DataFrame(health, dtype=object),
        "section_l11_to_l14": pd.DataFrame(columns=["uuid"], dtype=object),
    }
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    references = {name: pd.DataFrame(rows, dtype=object) for name, rows in metadata["references"].items()}
    return datasets, references
