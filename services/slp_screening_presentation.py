"""Explain saved SLP findings; never calculate or replace a screening status."""

from services.assessment_reference_labels import code_key, evidence_cell
from services.mpi_evidence import _member_heading
from services.screening.common import as_integer, is_missing, valid_code


PREVIOUS_ASSISTANCE = "Has the member previously received SLP assistance or undergone an SLP assessment?"
PATHWAYS = {
    "EF": {
        "label": "Employment Facilitation",
        "description": "Help with finding or preparing for work",
        "why": "SLP provides support for employment opportunities. The member's recorded work situation supports further review for employment assistance but does not establish eligibility.",
        "questions": (
            "Is the member currently employed or looking for work?",
            "What type of work or skills training is the member interested in?",
            "Does the member need help with job referrals, training, or employment requirements?",
        ),
    },
    "MD": {
        "label": "Microenterprise Development",
        "description": "Help with starting or improving a livelihood or small business",
        "why": "SLP supports people who want to start, maintain, or develop livelihood activities. The member's recorded work situation supports further review for livelihood assistance but does not establish eligibility.",
        "questions": (
            "What is the member's current work or livelihood situation?",
            "Is the member interested in starting a livelihood or business, or maintaining or improving an existing one?",
            "What challenges does the member face, and what training or livelihood assistance might help?",
        ),
    },
}
UNRESOLVED_QUESTIONS = (
    "What is the member's current employment or livelihood situation?",
    "Is the member interested in finding work or starting or improving a livelihood?",
    "What employment, skills training, or livelihood assistance does the member currently need?",
    PREVIOUS_ASSISTANCE,
)


def _age_text(raw, year):
    age = as_integer(raw)
    return f"Age {age} at the {year} CBMS survey." if age is not None else "Age at survey: Not recorded or unclear."


def slp_unresolved_members(source):
    """Describe applicable unanswered fields, without inferring review triggers.

    Known exclusions suppress irrelevant gaps. Nonwork skips worker class;
    the employment-nature question is never essential to SLP.
    """
    cards = []
    for member in source["members"]:
        answers = member["responses"]
        age = as_integer(answers.get("a07_age"))
        residency = valid_code(answers.get("b06_ofi"), range(1, 8))
        work = valid_code(answers.get("e01_employed"), (1, 2))
        worker = valid_code(answers.get("e18_class_of_worker"), range(7))
        if (age is not None and age < 16) or (residency is not None and residency != 7):
            continue
        if work == 2 and age is not None and age < 18:
            continue
        if work == 1 and worker is not None and worker not in (3, 4, 5, 6):
            continue
        if age is not None and age < 18 and worker is not None and worker not in (3, 4, 5, 6):
            continue
        details = []
        fields = {}
        if age is None:
            details.append("Age at survey is not recorded or unclear.")
            fields["a07_age"] = "The member's age at the survey was unclear, so the system could not confirm whether the SLP screening conditions applied."
        if residency is None:
            details.append("Residency information: Not recorded or unclear.")
            fields["b06_ofi"] = "The member's recorded residency information was unclear, so the system could not confirm whether the SLP screening conditions applied."
        if work is None:
            details.append(
                "Work status: Not recorded. No answer was recorded about whether the member worked during the week before the survey."
                if is_missing(answers.get("e01_employed")) else
                "Work status: Unclear. The recorded answer about work during the week before the survey needs confirmation."
            )
            fields["e01_employed"] = (
                f"Work status was not recorded for this member in the {source['context']['survey_year']} CBMS."
                if is_missing(answers.get("e01_employed")) else
                "The member's recorded work status was unclear, so the system could not establish whether their recorded situation matched an SLP screening condition."
            )
        if work == 1 and worker is None:
            details.append("Employment arrangement: Not recorded or unclear for a member who reported working during the week before the survey.")
            fields["e18_class_of_worker"] = (
                "The member reported working, but their employment arrangement was not recorded. The system could not establish a livelihood screening match."
                if is_missing(answers.get("e18_class_of_worker")) else
                "The member reported working, but their recorded employment arrangement was unclear. The system could not establish a livelihood screening match."
            )
        if details:
            cards.append({"subject": _member_heading(member),
                          "line_number": code_key(member["line_number"]),
                          "unresolved_fields": fields,
                          "age": _age_text(answers.get("a07_age"), source["context"]["survey_year"]),
                          "details": details})
    return cards


def prepare_slp_guidance(finding, source, index, issues):
    """Pathway badges require saved trigger evidence, never source-only inference."""
    status = finding["screening_status"]
    year = source["context"]["survey_year"]
    members = {code_key(m["line_number"]): m for m in source["members"]}
    cards, pathways = [], []
    pathway_members = {}
    seen = set()
    if status == "For Review":
        for item in finding["supporting_evidence"]:
            pathway = item.get("pathway")
            if item.get("evidence_role", "trigger") != "trigger" or pathway not in PATHWAYS:
                continue
            line = code_key(item.get("member_line_number"))
            if line is not None:
                pathway_members.setdefault(pathway, set()).add(line)
            observed = item.get("observed_values", {})
            # Repeated supporting fields need not duplicate an identical card;
            # the full saved evidence remains available in the disclosure.
            key = (line, pathway, code_key(observed.get("a07_age")),
                   code_key(observed.get("e01_employed")),
                   code_key(observed.get("e18_class_of_worker")) if pathway == "MD" else None)
            if key in seen:
                continue
            seen.add(key)
            member = members.get(line)
            condition = "Reported not working during the week before the survey."
            if pathway == "EF" and valid_code(observed.get("e01_employed"), (1, 2)) != 2:
                condition = "The saved employment-assistance finding needs confirmation; recorded nonwork is not established by the saved answers."
            if pathway == "MD":
                cell = evidence_cell("e18_class_of_worker", observed.get("e18_class_of_worker"), "section_a_e_indiv", index)
                condition = cell["meaning"]
                if valid_code(observed.get("e18_class_of_worker"), (3, 4, 5, 6)) is None or "verified" in condition.lower():
                    condition = "The saved livelihood-assistance finding's work arrangement needs confirmation."
            cards.append({"subject": _member_heading(member) if member else "Member not identified in the available roster",
                          "age": _age_text(observed.get("a07_age"), year),
                          "details": [condition], "pathway": PATHWAYS[pathway]})
            if pathway not in pathways:
                pathways.append(pathway)
        # Each explanation describes only the members in that saved pathway.
        why = [
            PATHWAYS[p]["why"].replace(
                "The member's recorded work situation supports",
                "The members' recorded work situations support",
            ) if len(pathway_members.get(p, ())) > 1 else PATHWAYS[p]["why"]
            for p in pathways
        ]
        questions = [q for p in pathways for q in PATHWAYS[p]["questions"]]
        questions.append(PREVIOUS_ASSISTANCE)
        situation = "employment and livelihood" if len(pathways) == 2 else "livelihood" if pathways == ["MD"] else "employment"
        member_count = len(set().union(*pathway_members.values()))
        circumstances = (
            f"the members' current {situation} situations"
            if member_count > 1 else f"the member's current {situation} situation"
        )
        action = f"Start an SLP Staff Review to verify {circumstances} and assistance needs, and document the findings."
        heading = "Why this was flagged"
        if not cards:
            cards = [{"subject": "Saved finding", "details": ["The saved result calls for review, but its specific supporting condition needs confirmation."]}]
            why = [cards[0]["details"][0]]
    elif status == "Needs Data Review":
        cards = slp_unresolved_members(source)
        work_missing_only = cards and all(
            len(c["details"]) == 1 and c["details"][0].startswith("Work status: Not recorded") for c in cards
        )
        # Count roster identities, not repeated evidence or display cards.
        affected_count = len({card["line_number"] for card in cards})
        number = {1: "One", 2: "Two"}.get(affected_count, str(affected_count))
        work_summary = (
            "One household member's work status was not recorded"
            if affected_count == 1 else
            f"{number} household members' work statuses were not recorded"
        )
        why = [
            f"{work_summary} in the {year} CBMS. Without this information, "
            "the system could not establish an SLP screening condition."
            if work_missing_only else
            "The available historical information needs clarification before the saved SLP screening finding can be explained reliably. Check the specific information shown above."
        ]
        if not cards:
            cards = [{"subject": "Information to clarify", "details": ["The saved finding does not identify one reliable cause. Some questions may have been skipped as expected; a missing answer does not mean No."]}]
        heading, questions = "Why more information is needed", list(UNRESOLVED_QUESTIONS)
        circumstances = "the members'" if affected_count > 1 else "the member's"
        action = f"Staff may start an SLP Review to verify {circumstances} current circumstances and document findings."
    else:
        cards = [{"subject": f"CBMS {year}", "details": ["No recorded employment or livelihood condition matched the system's preliminary SLP screening rules."]}]
        heading = "Why there is no review signal"
        why = [f"The available {year} CBMS records did not identify a household member meeting the conditions for an SLP review. This does not mean the household is ineligible or does not need assistance today."]
        questions = [f"Has any member's employment or livelihood situation changed since the {year} survey?",
                     "Does any member currently need help finding work, receiving training, or starting or improving a livelihood?",
                     "Has any member previously received SLP assistance or undergone an SLP assessment?"]
        action = f"No review is suggested based on the {year} findings. Staff may still start an SLP Review if newer information indicates a possible need for assistance."
    # Existing warnings/differences remain visible. Broad roster-age checks are
    # replaced by the applicable field descriptions above for SLP only.
    clarifications = [issue for issue in issues if "age is missing or unclear" not in issue]
    if status == "For Review":
        clarifications.extend(
            card["subject"] + ": " + " ".join(card["details"])
            for card in slp_unresolved_members(source)
        )
    return {"found": cards, "why_heading": heading, "why": why,
            "clarifications": clarifications,
            "questions": list(dict.fromkeys(questions)), "action": action}
