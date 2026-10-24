"""Explain saved TUPAD findings; never calculate or replace a screening status."""

from services.assessment_reference_labels import code_key
from services.mpi_evidence import _member_heading
from services.screening.common import as_integer, is_missing, valid_code
from services.slp_screening_presentation import _age_text
from services.subsidy_screening_content import REASON_TEXT

UNKNOWN_MEMBER = "Member not identified in the available roster"
UNSUPPORTED_REASON = "The saved finding's supporting condition needs confirmation."
WHY_REVIEW = (
    "TUPAD provides short-term emergency employment for disadvantaged or displaced "
    "workers. The {member} recorded work {situation} further review but "
    "{does} not establish eligibility or current unemployment."
)
QUESTIONS = {
    "For Review": (
        "Is the member currently without work, or working only short-term, seasonally, or occasionally?",
        "Has the member's work situation changed since the {year} survey?",
        "Is the member available for short-term emergency work?",
        "Has the member previously received TUPAD or other emergency employment assistance?",
    ),
    "Needs Data Review": (
        "What is the member's current work situation?",
        "Is the member without work, or working only short-term, seasonally, or occasionally?",
        "Is the member available for short-term emergency work?",
        "Has the member previously received TUPAD or other emergency employment assistance?",
    ),
    "No Historical CBMS Trigger": (
        "Has any adult member's work situation changed since the {year} survey?",
        "Is any adult member currently without work, or working only short-term, seasonally, or occasionally?",
        "Has any member previously received TUPAD or other emergency employment assistance?",
    ),
}


def tupad_unresolved_members(source):
    """Describe unanswered fields that blocked a screening decision for adult residents.

    Mirrors the engine's skips: minors, members outside B06=7 and those who
    worked regularly need no further answers.
    """
    cards = []
    for member in source["members"]:
        answers = member["responses"]
        age = as_integer(answers.get("a07_age"))
        residency = valid_code(answers.get("b06_ofi"), range(1, 8))
        work = valid_code(answers.get("e01_employed"), (1, 2))
        nature = valid_code(answers.get("e11_nature_of_employment"), (1, 2, 3))
        if (age is not None and age < 18) or (residency is not None and residency != 7):
            continue
        if work == 1 and nature == 1:
            continue
        details = []
        if age is None:
            details.append("Age at survey is not recorded or unclear.")
        if residency is None:
            details.append("Residency information is not recorded or unclear.")
        if age is not None and residency is not None:
            if work is None:
                details.append(
                    "Work status was not recorded for the week before the survey."
                    if is_missing(answers.get("e01_employed")) else
                    "The recorded answer about work during the week before the survey needs confirmation."
                )
            elif work == 1 and nature is None:
                details.append(
                    "The member reported working, but the nature of the work was not "
                    "recorded or is unclear."
                )
        if details:
            cards.append({"subject": _member_heading(member), "details": details,
                          "line_number": code_key(member["line_number"])})
    return cards


def _review_cards(finding, source):
    """Only saved trigger evidence creates a finding; context stays in the disclosure."""
    members = {code_key(m["line_number"]): m for m in source["members"]}
    year = source["context"]["survey_year"]
    cards, seen = [], set()
    for item in finding["supporting_evidence"]:
        if item.get("evidence_role", "trigger") != "trigger":
            continue
        line = code_key(item.get("member_line_number"))
        kind = item.get("trigger_code")
        if (line, kind) in seen:
            continue
        seen.add((line, kind))
        member = members.get(line)
        cards.append({
            "subject": _member_heading(member) if member else UNKNOWN_MEMBER,
            "age": _age_text(item.get("observed_values", {}).get("a07_age"), year),
            "details": [REASON_TEXT.get(kind, UNSUPPORTED_REASON)],
            "line_number": line,
        })
    return cards


def prepare_tupad_guidance(finding, source, issues):
    """Return the staff-facing explanation for one stored TUPAD finding."""
    status = finding["screening_status"]
    year = source["context"]["survey_year"]
    unresolved = tupad_unresolved_members(source)
    # Age gaps are described by the member cards, so avoid repeating them.
    clarifications = [issue for issue in issues if "age is missing or unclear" not in issue]
    if status == "For Review":
        cards = _review_cards(finding, source)
        plural = len({card["line_number"] for card in cards}) > 1
        why = [WHY_REVIEW.format(
            member="members'" if plural else "member's",
            situation="situations support" if plural else "situation supports",
            does="do" if plural else "does",
        ) if cards else UNSUPPORTED_REASON]
        if not cards:
            cards = [{"subject": "Saved finding", "details": [UNSUPPORTED_REASON]}]
        heading = "Why this was flagged"
        action = "Start a TUPAD Staff Review to verify the member's current work situation and document the findings."
    elif status == "Needs Data Review":
        cards = unresolved or [{
            "subject": "Information to clarify",
            "details": clarifications or [
                "The saved finding does not identify one reliable cause. Some questions "
                "may have been skipped as expected; a missing answer does not mean No."],
        }]
        heading = "Why more information is needed"
        why = ["The available historical information needs clarification before a TUPAD "
               "screening condition can be established. Check the specific information shown above."]
        action = "Staff may start a TUPAD Review to verify current work circumstances and document findings."
    else:
        cards = [{"subject": f"CBMS {year}", "details": [
            "No recorded adult work situation matched the system's preliminary TUPAD screening rules."]}]
        heading = "Why there is no review signal"
        why = [f"The available {year} CBMS records did not identify an adult member meeting the "
               "conditions for a TUPAD review. This does not mean the household is ineligible "
               "or does not need assistance today."]
        action = (f"No review is suggested based on the {year} findings. Staff may still start a "
                  "TUPAD Review if newer information indicates a possible need for assistance.")
    if status == "For Review":
        clarifications.extend(
            f"{card['subject']}: {' '.join(card['details'])}" for card in unresolved
        )
    return {"found": cards, "why_heading": heading, "why": why,
            "clarifications": clarifications, "action": action,
            "questions": [q.format(year=year) for q in QUESTIONS[status]]}
