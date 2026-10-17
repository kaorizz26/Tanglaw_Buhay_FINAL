"""Explicit synthetic stored findings and source answers for detail presentation tests."""

import json
from pathlib import Path

from repositories.assessment_runs import MPI_AUDIT_FIELDS, PROGRAM_TUPLES
from services.screening.metadata import OUTPUT_COLUMNS
from services.vulnerability.scoring import INDICATOR_WEIGHTS
from services.mpi_indicator_content import HOUSEHOLD_FIELDS


HOUSEHOLD_UUID = "00000000-0000-4000-8000-000000000001"
OTHER_UUID = "00000000-0000-4000-8000-000000000002"
ROOT = Path(__file__).resolve().parents[1]


def detail_source():
    """Handcraft stored outcomes; no MPI or subsidy engine is executed."""
    flags = dict.fromkeys(INDICATOR_WEIGHTS, 0)
    flags.update({
        "School Attendance": 1, "Food Insecurity": 1,
        "Water": 1, "Employment Proxy": 1, "Internet": None,
    })
    mpi = {
        **flags, "uuid": HOUSEHOLD_UUID, "vulnerability_score": 36.11111111111111,
        "vulnerability_rank": 7, "deprived_indicator_count": 4,
        "not_deprived_indicator_count": 11, "unknown_indicator_count": 1,
        "known_weight": 35 / 36, "coverage_pct": 100 * 35 / 36,
        "data_quality_status": "Needs Data Review",
        "unknown_zero_point_indicators": ("Internet",),
        "assumptions_used": ("Unknown indicators contribute zero points",),
        **dict.fromkeys(MPI_AUDIT_FIELDS, 0),
    }
    programs = {}
    for code, status in (
        ("4Ps", "For Review"), ("SLP", "Needs Data Review"),
        ("SPISC", "No Historical CBMS Trigger"), ("TUPAD", "For Review"),
    ):
        program = dict.fromkeys(OUTPUT_COLUMNS, "synthetic")
        program.update(dict.fromkeys(PROGRAM_TUPLES, ()))
        program.update(household_uuid=HOUSEHOLD_UUID, program_code=code,
                       survey_year=2022, screening_status=status)
        programs[code] = program
    household = dict.fromkeys(HOUSEHOLD_FIELDS, "0")
    household.update({
        "q01_main_water": "3", "q14_toilet_facility": "12", "q19_share_toilet": "2",
        "r03_roof": "1", "r04_outer_walls": "1", "r06_floor": "1",
        "r07_floor_area": "50", "r10_tenure": "1", "r12_electricity": "1",
        "n01_internet_access": "NA", "h01_access_to_public_transportation": "1",
        "l33_member_who_has_been_sick": "2", "r16_n_mobile_phone": "1",
        "r16_a_refrigerator": "1",
    })
    for number, field in enumerate(field for field in HOUSEHOLD_FIELDS if field.startswith("j0")):
        household[field] = "1" if number < 4 else "2"
    members = []
    for line, age in ((1, "45"), (2, "12"), (3, "80")):
        members.append({
            "line_number": line, "health_present": True,
            "responses": {
                "a01_first_name": "Synthetic", "a01_middle_name": "",
                "a01_last_name": f"Member {line}", "a07_age": age,
                "b06_ofi": "7" if line != 2 else "NA", "e01_employed": "2",
                "e15_want_more_hours": "NA", "c02_hgc_group": "7",
                "c03_currently_attending_school": "2" if line == 2 else "NA",
            },
            "health_responses": {"l34_has_been_sick": "2", "l38_availed_medical_treatment": "NA"},
        })
    return {
        "context": {"run_id": 2, "batch_id": 1, "survey_year": 2022,
                    "municipality": "Pozorrubio", "household_count": 51},
        "findings": {"household_uuid": HOUSEHOLD_UUID, "survey_year": 2022,
                     "mpi": mpi, "programs": programs},
        "identity": {"source_row_number": 8, "head_count": 1,
                     "first_name": "Synthetic", "middle_name": "", "last_name": "Member 1",
                     "barangay": "Synthetic Barangay"},
        "household": household, "members": members,
        "references": json.loads((ROOT / "fixtures/development_metadata.json").read_text(
            encoding="utf-8",
        ))["references"],
    }


def screening_source():
    """Explicit stored screening evidence; no screening or MPI calculation runs."""
    from services.screening.metadata import UNVERIFIED_REQUIREMENTS

    source = detail_source()
    source["household"].update({
        "l01_with_member_who_has_been_pregnant": "2",
        "l06_with_member_currently_pregnant": "2", "l15_with_pwd_member": "1",
    })
    for member in source["members"]:
        line = member["line_number"]
        member["responses"].update({
            "a02_relation_to_hh_head": "1" if line == 1 else "3", "a05_sex": "2",
            "b06_ofi": "7", "e01_employed": "NA" if line == 1 else "1",
            "e11_nature_of_employment": "2" if line == 3 else "NA",
            "e18_class_of_worker": "3" if line == 3 else "NA",
            "c07_tvet_graduate": "2", "c08_tvet_currently_attending": "2",
        })
        member["health_responses"].update(l07_currently_pregnant="2", l16_pwd="2")

    def evidence(line, fields, **extra):
        member = source["members"][line - 1]
        return {"member_line_number": line, "source_dataset": "section_a_e_indiv",
                "observed_values": {field: member["responses"][field] for field in fields},
                "explanation": "INTERNAL_TECHNICAL_EXPLANATION", **extra}

    evidence_by_program = {
        "4Ps": (evidence(2, ("a07_age",)),),
        "SLP": (evidence(3, ("a07_age", "b06_ofi", "e01_employed", "e18_class_of_worker"), pathway="MD"),),
        "SPISC": (evidence(3, ("a07_age",)),),
        "TUPAD": (
            evidence(1, ("a07_age", "b06_ofi", "e01_employed"),
                     trigger_code="assumed_nonwork", evidence_type="pilot_assumption"),
            evidence(3, ("a07_age", "b06_ofi", "e01_employed", "e11_nature_of_employment"),
                     trigger_code="irregular_work", evidence_type="observed"),
        ),
    }
    for code, finding in source["findings"]["programs"].items():
        finding.update(screening_status="For Review", supporting_evidence=evidence_by_program[code],
                       trigger_reasons=("INTERNAL_TECHNICAL_REASON",),
                       key_unverified_requirements=UNVERIFIED_REQUIREMENTS[code])
    return source
