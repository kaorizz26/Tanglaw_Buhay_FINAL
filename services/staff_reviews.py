"""Explicit staff initiation, independent of immutable automated findings."""

import psycopg

from db import get_connection
from repositories import assessment_runs, staff_reviews
from services.assessment_details import prepare_assessment_detail
from services.assessment_queries import get_active_assessment_detail
from services.subsidy_screening_content import PROGRAM_NAMES, STATUS_LABELS


class StaffReviewError(RuntimeError):
    """Fixed error codes; never include private values or database details."""


def get_staff_review_detail(household_uuid):
    """Load the assessment and staff reviews from one read-only snapshot."""
    source = get_active_assessment_detail(household_uuid, include_staff_reviews=True)
    result = prepare_assessment_detail(source)
    active = {row['program']: row for row in source['staff_reviews']}
    result['staff_reviews'] = [
        {**program, 'full_name': PROGRAM_NAMES[program['name']],
         'review': active.get(program['name']),
         'requires_reason': program['status'] != 'For Review'}
        for program in result['programs']
    ]
    return result


def start_staff_review(household_uuid, program, starting_reason, staff_id,
                       batch_id, run_id, *, connection_factory=get_connection):
    """Validate the current stored finding and append one independent review.

    Confirmation context must still be active/designated. Shared row locks keep
    activation, designation and account changes from racing the committed insert.
    No calculation, source update or finding update is performed.
    """
    if program not in PROGRAM_NAMES:
        raise StaffReviewError('invalid_program')
    if (not isinstance(starting_reason, str) or '\x00' in starting_reason
            or len(starting_reason) > 1000):
        raise StaffReviewError('invalid_reason')
    if (not isinstance(household_uuid, str) or not household_uuid.strip()
            or len(household_uuid) > 128 or '\x00' in household_uuid
            or any(type(value) is not int or value < 1
                   for value in (staff_id, batch_id, run_id))):
        raise StaffReviewError('invalid_request')
    reason = starting_reason.strip() or None
    try:
        with connection_factory() as connection:
            with connection.transaction():
                if not staff_reviews.lock_selected_context(connection, batch_id, run_id):
                    raise StaffReviewError('context_changed')
                if not staff_reviews.authorized_staff_exists(connection, staff_id):
                    raise StaffReviewError('unauthorized')
                finding = assessment_runs.find_household_assessment(
                    connection, run_id, household_uuid,
                )
                if finding is None:
                    raise StaffReviewError('household_missing')
                status = finding['programs'][program]['screening_status']
                if status not in STATUS_LABELS:
                    raise StaffReviewError('unavailable')
                if status != 'For Review' and reason is None:
                    raise StaffReviewError('reason_required')
                review_id = staff_reviews.insert_active(
                    connection, batch_id=batch_id, household_uuid=household_uuid,
                    run_id=run_id, program=program, starting_reason=reason, staff_id=staff_id,
                )
                if review_id is None:
                    raise StaffReviewError('already_active')
                return review_id
    except StaffReviewError:
        raise
    except (psycopg.Error, RuntimeError, KeyError, TypeError):
        raise StaffReviewError('unavailable') from None
