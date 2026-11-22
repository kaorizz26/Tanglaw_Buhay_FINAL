"""Read-only household screening and staff-review information for one citizen.

The signed-in Citizen Account supplies the household identity. Callers cannot
choose another household, and this service never runs an assessment engine or
changes a stored finding, review, or source record.
"""

from services.assessment_queries import (
    AssessmentQueryError,
    get_active_assessment_detail,
)
from services.mpi_evidence import build_evidence_index
from services.subsidy_screening_content import STATUS_LABELS
from services.subsidy_screening_details import prepare_subsidy_screening


PROGRAM_SLUGS = {
    "4Ps": "4ps",
    "SLP": "slp",
    "SPISC": "spisc",
    "TUPAD": "tupad",
}

CITIZEN_STATUS_EXPLANATIONS = {
    "For Review": (
        "The historical household record contains a condition that may warrant LGU review."
    ),
    "Needs Data Review": (
        "The historical household record does not contain enough clear information to "
        "establish a screening match."
    ),
    "No Historical CBMS Trigger": (
        "No matching condition was found in the available historical household record."
    ),
}


class CitizenHouseholdError(RuntimeError):
    """Household information could not be safely shown."""


def _current_reviews(rows):
    """Return the one current stored review row for each subsidy program."""
    return {
        row["program"]: row
        for row in rows
        if row["id"] == row["current_review_id"]
    }


def _present_review(row):
    """Describe a staff review without presenting it as subsidy approval."""
    if row is None:
        return {
            "status": "not_started",
            "status_label": "Not Started",
            "status_class": "not-started",
            "message": "The LGU office has not started a staff review for this program.",
            "feedback": None,
            "updated_at": None,
        }
    if row["status"] == "In Review":
        return {
            "status": "in_review",
            "status_label": "Under Review",
            "status_class": "in-review",
            "message": "LGU staff are reviewing the household information for this program.",
            "feedback": None,
            "updated_at": row["created_at"],
        }
    if row["status"] == "Closed" and row["withdrawn"]:
        return {
            "status": "withdrawn",
            "status_label": "Recommendation Withdrawn",
            "status_class": "withdrawn",
            "message": "The earlier staff recommendation was withdrawn and is no longer current.",
            "feedback": None,
            "updated_at": row["effective_at"] or row["finished_at"],
        }
    if row["status"] == "Closed":
        recommendation = row["effective_recommendation"]
        return {
            "status": "completed",
            "status_label": recommendation,
            "status_class": (
                "follow-up" if recommendation == "For LGU Follow-Up" else "no-action"
            ),
            "message": (
                "LGU staff recommend further consideration by the appropriate office."
                if recommendation == "For LGU Follow-Up"
                else "LGU staff recorded that no additional action is recommended at this time."
            ),
            "feedback": row["effective_explanation"],
            "updated_at": row["effective_at"] or row["finished_at"],
        }
    return {
        "status": "cancelled",
        "status_label": "Review Ended",
        "status_class": "cancelled",
        "message": "The staff review ended without a recommendation.",
        "feedback": None,
        "updated_at": row["cancelled_at"] or row["created_at"],
    }


def _present_program(program, review):
    """Keep the stored finding and a later staff review visibly separate."""
    status = program["status"]
    if program["code"] == "SPISC":
        status = program["guidance"]["current_status"]
    return {
        "code": program["code"],
        "name": program["name"],
        "slug": PROGRAM_SLUGS[program["code"]],
        "screening_status": status,
        "screening_label": STATUS_LABELS[status],
        "screening_class": {
            "For Review": "review",
            "Needs Data Review": "data",
            "No Historical CBMS Trigger": "none",
        }[status],
        "screening_explanation": CITIZEN_STATUS_EXPLANATIONS[status],
        "reasons": [
            {"subject": reason["subject"], "text": reason["text"]}
            for reason in program["reasons"]
        ],
        "needs_information": bool(program["issues"]),
        "review": _present_review(review),
    }


def prepare_citizen_household(source, account):
    """Prepare only citizen-safe fields after verifying the account linkage."""
    linked_account = source.get("citizen_account")
    if (
        source["context"]["batch_id"] != account["batch_id"]
        or source["findings"]["household_uuid"] != account["household_uuid"]
        or not isinstance(linked_account, dict)
        or linked_account.get("id") != account["id"]
    ):
        raise CitizenHouseholdError("household_scope_mismatch")

    screening = prepare_subsidy_screening(source, build_evidence_index(source))
    reviews = _current_reviews(source.get("staff_reviews", []))
    programs = [
        _present_program(program, reviews.get(program["code"]))
        for program in screening
    ]
    return {
        "state": "ready",
        "survey_year": source["context"]["survey_year"],
        "programs": programs,
        "review_count": sum(
            program["review"]["status"] != "not_started" for program in programs
        ),
        "screening_count": sum(
            program["screening_status"] == "For Review" for program in programs
        ),
    }


def get_citizen_household(account, *, connection_factory=None):
    """Read the signed-in citizen's linked active household assessment."""
    options = {"include_staff_reviews": True}
    if connection_factory is not None:
        options["connection_factory"] = connection_factory
    try:
        source = get_active_assessment_detail(account["household_uuid"], **options)
    except AssessmentQueryError as problem:
        raise CitizenHouseholdError("household_information_unavailable") from problem
    try:
        return prepare_citizen_household(source, account)
    except CitizenHouseholdError:
        raise
    except (KeyError, TypeError, ValueError) as problem:
        raise CitizenHouseholdError("household_information_unavailable") from problem


def build_citizen_updates(household, documents):
    """Combine citizen-visible review and document changes into newest-first updates."""
    updates = []
    if household.get("state") == "ready":
        for program in household["programs"]:
            review = program["review"]
            if review["updated_at"] is None:
                continue
            updates.append({
                "kind": "review",
                "title": f"{program['code']} staff review",
                "message": f"{review['status_label']}. {review['message']}",
                "updated_at": review["updated_at"],
                "status_class": review["status_class"],
            })
    for document in documents or []:
        updated_at = document.get("updated_at") or document.get("created_at")
        if updated_at is None:
            continue
        updates.append({
            "kind": "document",
            "title": document["title"],
            "message": f"Document status: {document['status_label']}.",
            "updated_at": updated_at,
            "status_class": document["status"],
        })
    return sorted(updates, key=lambda item: item["updated_at"], reverse=True)
