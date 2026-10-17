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
    history = source['staff_reviews']
    active = {row['program']: row for row in history if row['status'] == 'In Review'}
    result['staff_reviews'] = [
        {**program, 'full_name': PROGRAM_NAMES[program['name']],
         'review': active.get(program['name']),
         'history': [row for row in history if row['program'] == program['name']],
         'latest': next((row for row in reversed(history)
                         if row['program'] == program['name']), None),
         'requires_reason': program['status'] != 'For Review'}
        for program in result['programs']
    ]
    return result


RECOMMENDATIONS = ('For LGU Follow-Up', 'No Further Action')


def get_assignable_staff(*, connection_factory=get_connection):
    try:
        with connection_factory() as connection:
            return staff_reviews.list_assignable_staff(connection)
    except (psycopg.Error, RuntimeError):
        raise StaffReviewError('unavailable') from None


def act_on_review(household_uuid, program, review_id, action, staff_id,
                  batch_id, run_id, *, text='', recommendation='',
                  responsible_staff_id=None, responsibility_version=None,
                  connection_factory=get_connection):
    """Serialize all actions on a review; never alter source or assessment results."""
    if (program not in PROGRAM_NAMES or action not in ('note', 'finish', 'cancel', 'reassign')
            or not isinstance(household_uuid, str) or not household_uuid.strip()
            or len(household_uuid) > 128 or '\x00' in household_uuid
            or any(type(value) is not int or value < 1
                   for value in (review_id, staff_id, batch_id, run_id))):
        raise StaffReviewError('invalid_request')
    if action == 'reassign' and any(type(value) is not int or value < 1 for value in (
            responsible_staff_id, responsibility_version)):
        raise StaffReviewError('invalid_assignee')
    limit = 1000 if action in ('cancel', 'reassign') else 2000
    if not isinstance(text, str) or '\x00' in text or len(text) > limit:
        raise StaffReviewError('invalid_text')
    text = text.strip()
    if action != 'cancel' and not text:
        raise StaffReviewError('text_required')
    if action == 'finish' and recommendation not in RECOMMENDATIONS:
        raise StaffReviewError('invalid_recommendation')
    try:
        with connection_factory() as connection:
            with connection.transaction():
                if not staff_reviews.lock_selected_context(connection, batch_id, run_id):
                    raise StaffReviewError('context_changed')
                if not staff_reviews.authorized_staff_exists(connection, staff_id):
                    raise StaffReviewError('unauthorized')
                review = staff_reviews.lock_review(
                    connection, review_id, batch_id, household_uuid, program,
                )
                if review is None:
                    raise StaffReviewError('review_missing')
                if review['status'] != 'In Review':
                    raise StaffReviewError('not_active')
                if action == 'reassign':
                    if review['responsibility_version'] != responsibility_version:
                        raise StaffReviewError('assignment_changed')
                    if review['responsible_staff_id'] == responsible_staff_id:
                        raise StaffReviewError('same_assignee')
                    if not staff_reviews.authorized_staff_exists(connection, responsible_staff_id):
                        raise StaffReviewError('invalid_assignee')
                    staff_reviews.reassign(connection, review, staff_id, responsible_staff_id, text)
                elif action == 'note':
                    staff_reviews.add_note(connection, review_id, staff_id, text)
                elif action == 'finish':
                    staff_reviews.finish(connection, review_id, staff_id, recommendation, text)
                else:
                    staff_reviews.cancel(connection, review_id, staff_id, text or None)
    except StaffReviewError:
        raise
    except (psycopg.Error, RuntimeError, KeyError, TypeError):
        raise StaffReviewError('unavailable') from None


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
