"""Project priority policy over stored findings, separate from eligibility/reviews."""

from services.screening.metadata import PROGRAMS


SECONDARY_MIN_SCORE = 20
PRIMARY_CUTOFF = 25
PRIMARY_PRIORITY = "Primary Priority"
SECONDARY_PRIORITY = "Secondary Priority"
NO_SUBSIDY_PRIORITY = "No Subsidy Priority"


def derive_subsidy_priority(vulnerability_score, program_statuses):
    """Use stored scores/statuses only; Unknown indicators do not change the tier."""
    if not any(program_statuses.get(code) == "For Review" for code in PROGRAMS):
        return NO_SUBSIDY_PRIORITY
    if vulnerability_score > PRIMARY_CUTOFF:
        return PRIMARY_PRIORITY
    if vulnerability_score >= SECONDARY_MIN_SCORE:
        return SECONDARY_PRIORITY
    return NO_SUBSIDY_PRIORITY
