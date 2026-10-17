"""Append completed recommendation changes without rewriting original decisions."""

import psycopg

from db import get_connection
from repositories import staff_reviews
from services.staff_reviews import RECOMMENDATIONS, StaffReviewError
from services.subsidy_screening_content import PROGRAM_NAMES


def change_recommendation(household_uuid, program, review_id, action, staff_id,
                          batch_id, run_id, *, version, reason, recommendation='',
                          explanation='', save=True, connection_factory=get_connection):
    """Validate previews and saves identically; serialize with starts and changes."""
    if (program not in PROGRAM_NAMES or action not in ('correct', 'withdraw')
            or not isinstance(household_uuid, str) or not household_uuid.strip()
            or len(household_uuid) > 128 or '\x00' in household_uuid
            or any(type(value) is not int or value < 1
                   for value in (review_id, staff_id, batch_id, run_id))
            or type(version) is not int or version < 0):
        raise StaffReviewError('invalid_request')
    if (not isinstance(reason, str) or '\x00' in reason or len(reason) > 1000
            or not reason.strip()):
        raise StaffReviewError('invalid_change_reason')
    reason = reason.strip()
    if action == 'correct':
        if recommendation not in RECOMMENDATIONS:
            raise StaffReviewError('invalid_recommendation')
        if (not isinstance(explanation, str) or '\x00' in explanation
                or len(explanation) > 2000 or not explanation.strip()):
            raise StaffReviewError('invalid_change_explanation')
        explanation = explanation.strip()
    try:
        with connection_factory() as connection:
            with connection.transaction():
                if not staff_reviews.lock_selected_context(connection, batch_id, run_id):
                    raise StaffReviewError('context_changed')
                if not staff_reviews.authorized_staff_exists(connection, staff_id):
                    raise StaffReviewError('unauthorized')
                if not staff_reviews.lock_household_reviews(connection, batch_id, household_uuid):
                    raise StaffReviewError('review_missing')
                review = staff_reviews.lock_review(
                    connection, review_id, batch_id, household_uuid, program,
                )
                if review is None:
                    raise StaffReviewError('review_missing')
                state = staff_reviews.recommendation_state(connection, review_id)
                if (review['status'] != 'Closed' or state['withdrawn']
                        or not staff_reviews.is_current_review(connection, review)):
                    raise StaffReviewError('recommendation_unavailable')
                if state['recommendation_version'] != version:
                    raise StaffReviewError('recommendation_changed')
                if (action == 'correct'
                        and recommendation == state['effective_recommendation']
                        and explanation == state['effective_explanation']):
                    raise StaffReviewError('no_change')
                if save:
                    staff_reviews.append_recommendation_change(
                        connection, review_id, state, action, staff_id,
                        reason, recommendation, explanation,
                    )
                return state
    except StaffReviewError:
        raise
    except (psycopg.Error, RuntimeError, KeyError, TypeError):
        raise StaffReviewError('unavailable') from None
