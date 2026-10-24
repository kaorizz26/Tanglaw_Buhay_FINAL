"""Two-step confirmation within the existing focused review workspace."""

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from services.auth import csrf_token
from services.assessment_queries import AssessmentQueryError
from services.staff_reviews import StaffReviewError, get_staff_review_detail
from services.recommendation_changes import change_recommendation


def recommendation_form(household_uuid, program, review_id, mode, navigation):
    try:
        results = get_staff_review_detail(str(household_uuid))
    except AssessmentQueryError as error:
        abort(404 if str(error) == 'household_assessment_missing' else 503,
              description='This household review is unavailable. Please try again later.')
    record = next((row for row in results['review_history']
                   if row['id'] == review_id and row['program'] == program), None)
    if record is None:
        abort(404, description='This staff review is unavailable for this household.')
    if not record['can_change_recommendation']:
        abort(409, description='This recommendation can no longer be changed. '
              'Return to Staff Reviews to see the latest review.')
    signer = URLSafeTimedSerializer(current_app.secret_key, salt='review-recommendation-confirmation')
    identity = {'household_uuid': str(household_uuid), 'program': program,
                'review_id': review_id, 'action': mode, 'staff_id': g.user['id']}
    values = {'version': record['recommendation_version'], 'reason': '',
              'recommendation': '', 'explanation': '',
              'batch_id': results['context']['batch_id'], 'run_id': results['context']['run_id']}
    confirmation = False
    confirmation_token = ''
    error = None
    if request.method == 'POST':
        try:
            for key in ('step', 'confirmation_token', 'version', 'reason', 'recommendation',
                        'explanation', 'batch_id', 'run_id'):
                if len(request.form.getlist(key)) > 1:
                    raise StaffReviewError('invalid_request')
            step = request.form.get('step', '')
            if step in ('confirm', 'edit'):
                try:
                    draft = signer.loads(request.form.get('confirmation_token', ''), max_age=1800)
                except BadSignature:
                    raise StaffReviewError('invalid_request') from None
                if draft['identity'] != identity:
                    raise StaffReviewError('invalid_request')
                values = draft['values']
            elif step == 'review':
                for key in ('version', 'batch_id', 'run_id'):
                    value = request.form.get(key, '')
                    if not value.isascii() or not value.isdecimal() or len(value) > 18:
                        raise StaffReviewError('invalid_request')
                    values[key] = int(value)
                for key in ('reason', 'recommendation', 'explanation'):
                    values[key] = request.form.get(key, '')
            else:
                raise StaffReviewError('invalid_request')
            change_recommendation(
                **identity, **values, save=step == 'confirm',
            )
            if step == 'confirm':
                flash('Recommendation corrected. The updated recommendation is shown below.'
                      if mode == 'correct' else
                      'Recommendation withdrawn. The completed review remains in history.')
                return redirect(url_for(
                    'assessments.review_record', household_uuid=household_uuid,
                    review_program=program, review_id=review_id, **navigation['detail_parameters'],
                ), code=303)
            confirmation = step == 'review'
            if confirmation:
                confirmation_token = signer.dumps({'identity': identity, 'values': values})
        except StaffReviewError as failure:
            code = str(failure)
            messages = {
                'invalid_change_reason': 'Enter a reason of up to 1,000 characters.',
                'invalid_recommendation': 'Choose the corrected recommendation.',
                'invalid_change_explanation': 'Enter an updated explanation of up to 2,000 characters.',
                'no_change': 'Change the recommendation or its explanation before reviewing the correction.',
            }
            if code in messages:
                error = messages[code]
            elif code in ('recommendation_changed', 'recommendation_unavailable'):
                abort(409, description='This recommendation changed or a newer review started. '
                      'Reload the completed review before making another change.')
            elif code == 'unauthorized':
                abort(403)
            elif code == 'review_missing':
                abort(404)
            elif code in ('invalid_request', 'context_changed'):
                abort(400, description='This form expired or the selection changed. '
                      'Return to the completed review and try again.')
            else:
                abort(503, description='The change could not be saved. Check the review before trying again.')
    return render_template(
        'assessments/detail.html', results=results, tab='reviews', review_record=record,
        review_mode=mode, review_error=error, change_values=values,
        change_confirmation=confirmation, confirmation_token=confirmation_token,
        **navigation, role_label='LGU Staff',
        staff_username=g.user['username'], csrf_token=csrf_token(),
    ), (400 if error else 200)
