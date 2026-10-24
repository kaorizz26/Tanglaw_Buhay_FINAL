"""Decode joined SPISC supporting information without creating review triggers.

Field/type pairs follow the PSA 2022 questionnaire and imported definitions.
Yes/No and ID-type meanings come from the selected batch's reference index.
L16/L30 remain the documented project interpretation of the joined export;
household corroboration is required before attributing positive details.
"""

from services.assessment_reference_labels import code_key, evidence_cell


DISABILITY_TYPES = (
    ("l17_a_visual_disability", "l18_a_visual_disability_diagnosed", "Visual disability"),
    ("l17_b_deaf_or_hard_of_hearing", "l18_b_deaf_or_hard_of_hearing_diagnosed", "Deaf or hard of hearing"),
    ("l17_c_intellectual_disability", "l18_c_intellectual_disability_diagnosed", "Intellectual disability"),
    ("l17_d_learning_disability", "l18_d_learning_disability_diagnosed", "Learning disability"),
    ("l17_e_mental_disability", "l18_e_mental_disability_diagnosed", "Mental disability"),
    ("l17_f_physical_disability_orthopedic", "l18_f_physical_disability_diagnosed", "Physical disability (orthopedic)"),
    ("l17_g_psychosocial_disability", "l18_g_psychosocial_disability_diagnosed", "Psychosocial disability"),
    ("l17_h_speech_and_language_impairment", "l18_h_speech_and_language_impairment_diagnosed", "Speech and language impairment"),
)
HOUSEHOLD_LABELS = {
    "l15_with_pwd_member": "Household reported a member with disability",
    "l29_with_member_who_has_pwd_id": "Household reported a member with a PWD ID",
}
MEMBER_LABELS = {
    "l16_pwd": "Member disability response",
    **{field: label for field, _, label in DISABILITY_TYPES},
    **{field: f"Doctor's diagnosis reported — {label}" for _, field, label in DISABILITY_TYPES},
    "l30_with_pwd_id": "PWD ID reported",
    "l31_shown_pwd_id": "PWD ID reportedly shown at interview",
    "l32_type_of_disability_in_pwd_id": "Disability type recorded from the PWD ID",
}


def _missing(raw):
    return raw is None or str(raw).strip().upper() in ("", "NA")


def _cell(field, raw, index, *, applicable=True):
    """Decode one answer; valid No and expected skips receive no highlighting."""
    dataset = "household" if field in HOUSEHOLD_LABELS else "section_l02_to_l41"
    meaning = evidence_cell(field, raw, dataset, index)["meaning"]
    answer = {"yes": True, "no": False}.get(meaning.casefold())
    if _missing(raw):
        value = "Not recorded" if applicable else "Not asked for this recorded answer"
        unclear = applicable
    elif field == "l32_type_of_disability_in_pwd_id":
        unclear = "verification" in meaning.lower() or "verified" in meaning.lower()
        value = "Needs confirmation" if unclear else meaning
    else:
        unclear = answer is None
        value = meaning if not unclear else "Needs confirmation"
    return {
        "label": (HOUSEHOLD_LABELS | MEMBER_LABELS)[field], "value": value,
        "answer": answer, "needs_verification": unclear,
    }


def _member_information(member, household, index):
    """The repository already joins batch, household UUID and member line."""
    joined = member["health_present"]
    answers = member["health_responses"] if joined else {}
    hh_disability = household[0]["answer"]
    hh_id = household[1]["answer"]
    recorded, cells, notes = [], [], []

    def read(field, *, applicable=True):
        cell = _cell(field, answers.get(field), index,
                     applicable=applicable)
        recorded.append(cell)
        return cell

    disability = read("l16_pwd", applicable=hh_disability is not False)
    supported = joined and hh_disability is True and disability["answer"] is True
    if disability["answer"] is True and not supported:
        disability["needs_verification"] = True
        notes.append("The member's disability information needs household confirmation.")

    reported_types, diagnoses = [], []
    for type_field, diagnosis_field, label in DISABILITY_TYPES:
        reported = _cell(type_field, answers.get(type_field), index, applicable=supported)
        diagnosis = _cell(diagnosis_field, answers.get(diagnosis_field), index,
                          applicable=supported and reported["answer"] is True)
        if supported:
            if reported["answer"] is True:
                reported_types.append(label)
                diagnoses.append(f"{label}: {diagnosis['value']}")
                recorded.extend((reported, diagnosis))
        # Preserve populated downstream contradictions rather than assigning a
        # disability/diagnosis under a negative, missing or uncorroborated parent.
        for cell in (reported, diagnosis):
            if (not supported or reported["answer"] is not True) and cell["answer"] is True:
                cell["needs_verification"] = True
                if cell not in recorded:
                    recorded.append(cell)
                notes.append("Some disability details conflict with the recorded answers.")
    if supported:
        cells.append({"label": "Reported disability", "value": "; ".join(reported_types)
                      or "Type not recorded or unclear"})
        if not reported_types:
            recorded.append({"label": "Reported disability types",
                             "value": "No type identified; needs confirmation",
                             "needs_verification": True})
        if diagnoses:
            cells.append({"label": "Doctor's diagnosis reported", "value": "; ".join(diagnoses)})
        if not reported_types or any(c["needs_verification"] for c in recorded):
            notes.append("Some disability information needs confirmation.")
    elif joined and disability["answer"] is False:
        notes.append("No disability reported for this member.")
    elif joined and hh_disability is True and disability["answer"] is None:
        notes.append("Disability information for this member is not recorded or unclear.")

    pwd_id = _cell("l30_with_pwd_id", answers.get("l30_with_pwd_id"), index,
                   applicable=hh_id is not False)
    # L29 = No skips L30–L32; retain any actual recorded member answer.
    if not (hh_id is False and _missing(answers.get("l30_with_pwd_id"))):
        recorded.append(pwd_id)
    id_supported = joined and hh_id is True and pwd_id["answer"] is True
    if pwd_id["answer"] is True and not id_supported:
        pwd_id["needs_verification"] = True
        notes.append("The member's PWD ID information needs household confirmation.")
    if id_supported or (joined and pwd_id["answer"] is False):
        cells.append({"label": "PWD ID reported", "value": pwd_id["value"]})
    elif joined and hh_id is True and pwd_id["answer"] is None:
        notes.append("PWD ID information for this member is not recorded or unclear.")

    shown = _cell("l31_shown_pwd_id", answers.get("l31_shown_pwd_id"), index,
                  applicable=id_supported)
    id_type = _cell("l32_type_of_disability_in_pwd_id",
                    answers.get("l32_type_of_disability_in_pwd_id"), index,
                    applicable=id_supported and shown["answer"] is True)
    if id_supported:
        recorded.append(shown)
        cells.append({"label": shown["label"], "value": shown["value"]})
        if shown["answer"] is True:
            recorded.append(id_type)
            cells.append({"label": id_type["label"], "value": id_type["value"]})
        elif not _missing(answers.get("l32_type_of_disability_in_pwd_id")):
            id_type["needs_verification"] = True
            recorded.append(id_type)
            notes.append("The ID disability type needs confirmation; the ID was not reported as shown.")
    elif shown["answer"] is True or not _missing(answers.get("l32_type_of_disability_in_pwd_id")):
        for cell, field in ((shown, "l31_shown_pwd_id"),
                            (id_type, "l32_type_of_disability_in_pwd_id")):
            if not _missing(answers.get(field)):
                cell["needs_verification"] = True
                recorded.append(cell)
        notes.append("PWD ID details conflict with the recorded ID responses.")
    if not joined:
        notes = ["Member disability and PWD ID information is unavailable."]
    return {
        "cells": cells, "notes": list(dict.fromkeys(notes)), "recorded_cells": recorded,
        "disability_supported": supported, "id_supported": id_supported,
    }


def prepare_disability_information(source, index):
    """Return member-specific support and short, unattributed household messages."""
    household = [
        _cell(field, source["household"].get(field), index)
        for field in HOUSEHOLD_LABELS
    ]
    members = {
        code_key(member["line_number"]): _member_information(member, household, index)
        for member in source["members"]
    }
    notes = []
    if household[0]["answer"] is True and not any(
        item["disability_supported"] for item in members.values()
    ):
        notes.append("The household reported disability, but the affected member is not reliably identified.")
    elif household[0]["answer"] is False:
        notes.append("No disability was reported at household level.")
    elif household[0]["answer"] is None:
        notes.append("Household disability information is not recorded or unclear.")
    if household[1]["answer"] is True and not any(
        item["id_supported"] for item in members.values()
    ):
        notes.append("A PWD ID was reported in the household, but its holder is not reliably identified.")
    return {"household_cells": household, "members": members, "notes": notes}
