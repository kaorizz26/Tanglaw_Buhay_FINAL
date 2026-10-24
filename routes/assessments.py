"""Authenticated HTTP presentation of the explicitly selected stored assessment."""

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from services.assessment_queries import AssessmentQueryError
from services.assessment_filters import InvalidAssessmentFilters, parse_filter_arguments
from services.auth import ADMIN_ROLE, check_csrf, csrf_token
from services.prioritization import get_prioritization_page
from services.assessment_details import detail_navigation, get_assessment_detail
from services.staff_reviews import (
    StaffReviewError, get_staff_review_detail, start_staff_review,
    act_on_review, get_assignable_staff,
)
from services.subsidy_screening_content import PROGRAM_NAMES
from services.review_queue import (
    get_review_queue_page, parse_queue_arguments, parse_queue_return,
    queue_return_parameters,
)
from routes.recommendation_changes import recommendation_form


assessments = Blueprint("assessments", __name__)


def _assessment_navigation(arguments):
    tab, list_parameters = detail_navigation(arguments)
    queue_state = parse_queue_return(arguments)
    queue_parameters = {}
    queue_url = None
    if queue_state:
        page, filters = queue_state
        queue_parameters = queue_return_parameters(page, filters)
        queue_url = url_for('assessments.queue', page=page, **filters.parameters)
    return tab, {
        'list_parameters': list_parameters,
        'detail_parameters': {**list_parameters, **queue_parameters},
        'queue_return_url': queue_url,
    }


@assessments.get('/assessments/queue')
def queue():
    """Read explicit staff work through the existing staff authorization guard."""
    try:
        page, filters = parse_queue_arguments(
            request.args, default_scope='all' if g.user['role'] == ADMIN_ROLE else 'mine',
        )
        results = get_review_queue_page(page, filters, staff_id=g.user['id'])
    except InvalidAssessmentFilters:
        abort(400, description='Choose valid review status and filter values.')
    except AssessmentQueryError:
        abort(503, description='Staff reviews are unavailable. Please try again later.')
    if results is None:
        flash('This queue page is no longer available. Showing the first page.')
        return redirect(url_for('assessments.queue', page=1, **filters.parameters), code=303)
    return render_template(
        'assessments/queue.html', results=results,
        role_label='System Administrator' if g.user['role'] == ADMIN_ROLE else 'LGU Staff',
        staff_username=g.user['username'], csrf_token=csrf_token(),
    )


@assessments.get("/assessments")
def prioritization():
    """Render one stored summary page after the application's staff authorization."""
    if "view" in request.args:
        abort(
            410,
            description="This assessment view is not available while the interface is rebuilt.",
        )
    page_values = request.args.getlist("page")
    raw_page = page_values[0] if page_values else "1"
    if len(page_values) > 1 or not raw_page.isascii() or not raw_page.isdecimal():
        abort(400, description="Choose a valid page number.")
    if len(raw_page) > 9 or int(raw_page) < 1:
        abort(400, description="Choose a valid page number.")
    try:
        filters = parse_filter_arguments(request.args)
        results = get_prioritization_page(int(raw_page), filters)
    except InvalidAssessmentFilters:
        abort(400, description="Choose valid search and filter values.")
    except AssessmentQueryError:
        abort(503, description="Household assessments are unavailable. Please try again later.")
    if results is None:
        abort(404, description="This assessment page does not exist.")
    role_label = "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff"
    return render_template(
        "assessments/prioritization.html",
        results=results,
        role_label=role_label,
        staff_username=g.user["username"],
        csrf_token=csrf_token(),
    )


@assessments.get("/assessments/<uuid:household_uuid>")
def detail(household_uuid):
    """Show one stored household only after the unchanged staff authorization guard."""
    try:
        tab, navigation = _assessment_navigation(request.args)
        if tab == "reviews":
            results = get_staff_review_detail(str(household_uuid))
        else:
            results = get_assessment_detail(str(household_uuid))
    except InvalidAssessmentFilters:
        abort(400, description="Choose a valid assessment section and return filters.")
    except AssessmentQueryError as error:
        if str(error) == "household_assessment_missing":
            abort(404, description="This household assessment is unavailable in the selected batch.")
        abort(503, description="Household assessments are unavailable. Please try again later.")
    role_label = "System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff"
    return render_template(
        "assessments/detail.html", results=results, tab=tab,
        **navigation, role_label=role_label,
        staff_username=g.user["username"], csrf_token=csrf_token(),
    )


@assessments.route(
    "/assessments/<uuid:household_uuid>/reviews/<review_program>", methods=["GET", "POST"],
)
def start_review(household_uuid, review_program):
    """Confirm and explicitly initiate a review using the authenticated staff ID."""
    program = review_program
    if request.method == "POST":
        check_csrf()
    if program not in PROGRAM_NAMES:
        abort(404, description="Choose one of the available subsidy programs.")
    try:
        _, navigation = _assessment_navigation(request.args)
    except InvalidAssessmentFilters:
        abort(400, description="Choose valid return filters.")
    origins = request.args.getlist("origin")
    if len(origins) > 1:
        abort(400, description="Choose a valid starting point for this review.")
    # An unrecognized single value is ignored so the form returns to Staff Reviews.
    origin = "screening" if origins == ["screening"] else None
    error = None
    reason = ""
    review_id = None
    if request.method == "POST":
        try:
            for key in ("batch_id", "run_id", "starting_reason"):
                if len(request.form.getlist(key)) > 1:
                    raise StaffReviewError("invalid_request")
            context_ids = []
            for key in ("batch_id", "run_id"):
                value = request.form.get(key, "")
                if not value.isascii() or not value.isdecimal() or len(value) > 18:
                    raise StaffReviewError("invalid_request")
                context_ids.append(int(value))
            reason = request.form.get("starting_reason", "")
            review_id = start_staff_review(
                str(household_uuid), program, reason, g.user["id"], *context_ids,
            )
        except StaffReviewError as failure:
            code = str(failure)
            if code == "already_active":
                flash("A review is already in progress for this program.")
            elif code == 'recommendation_exists':
                flash('A completed recommendation already exists for this program. '
                      'Choose View Review to correct or withdraw it.')
            elif code in ("reason_required", "invalid_reason"):
                error = (
                    "Enter a brief reason for starting this review."
                    if code == "reason_required"
                    else "Use a reason of no more than 1,000 characters."
                )
            elif code == "unauthorized":
                abort(403, description="Your account cannot start this review.")
            elif code == "household_missing":
                abort(404, description=(
                    "This household assessment is unavailable in the selected batch."
                ))
            elif code in ("invalid_request", "context_changed"):
                abort(400, description=(
                    "The household selection changed or the form expired. "
                    "Return to Staff Reviews and try again."
                ))
            else:
                abort(503, description=(
                    "The review could not be saved. Return to Staff Reviews "
                    "to check its status before trying again."
                ))
        else:
            flash(f"The {program} staff review has started.")
        if error is None and review_id is not None:
            return redirect(url_for(
                "assessments.review_record", household_uuid=household_uuid,
                review_program=program, review_id=review_id,
                **navigation['detail_parameters'],
            ), code=303)
        if error is None:
            return redirect(url_for(
                "assessments.detail", household_uuid=household_uuid, tab="reviews",
                **navigation['detail_parameters'],
            ), code=303)
    try:
        results = get_staff_review_detail(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) == "household_assessment_missing":
            abort(404, description=(
                "This household assessment is unavailable in the selected batch."
            ))
        abort(503, description="Staff reviews are unavailable. Please try again later.")
    selected = next(
        item for item in results["staff_reviews"] if item["name"] == program
    )
    if selected['review'] or (selected['latest'] and selected['latest']['status'] == 'Closed'
                              and not selected['latest']['withdrawn']):
        if not selected['review']:
            flash('A completed recommendation already exists. '
                  'Choose View Review to correct or withdraw it.')
        return redirect(url_for(
            "assessments.detail", household_uuid=household_uuid, tab="reviews",
            **navigation['detail_parameters'],
        ), code=303)
    return render_template(
        "assessments/detail.html", results=results, tab="reviews", start_program=selected,
        review_error=error, starting_reason=reason[:1000], origin=origin, **navigation,
        role_label="System Administrator" if g.user["role"] == ADMIN_ROLE else "LGU Staff",
        staff_username=g.user["username"], csrf_token=csrf_token(),
    ), (400 if error else 200)


@assessments.route(
    '/assessments/<uuid:household_uuid>/reviews/<review_program>/<int:review_id>',
    methods=['GET', 'POST'],
)
def review_record(household_uuid, review_program, review_id):
    """Read history or perform one explicit, authenticated review action."""
    program = review_program
    if request.method == 'POST':
        check_csrf()
    if program not in PROGRAM_NAMES or review_id < 1:
        abort(404)
    try:
        _, navigation = _assessment_navigation(request.args)
    except InvalidAssessmentFilters:
        abort(400, description='Choose valid return filters.')
    mode = request.args.get('action', '')
    if len(request.args.getlist('action')) > 1 or mode not in (
            '', 'finish', 'cancel', 'reassign', 'correct', 'withdraw'):
        abort(400, description='Choose a valid review action.')
    if mode in ('correct', 'withdraw'):
        return recommendation_form(household_uuid, program, review_id, mode, navigation)
    error = None
    text = ''
    recommendation = ''
    selected_staff = ''
    error_status = 400
    if request.method == 'POST':
        try:
            for key in ('batch_id', 'run_id', 'action', 'text', 'recommendation',
                        'responsible_staff_id', 'responsibility_version'):
                if len(request.form.getlist(key)) > 1:
                    raise StaffReviewError('invalid_request')
            context = []
            for key in ('batch_id', 'run_id'):
                value = request.form.get(key, '')
                if not value.isascii() or not value.isdecimal() or len(value) > 18:
                    raise StaffReviewError('invalid_request')
                context.append(int(value))
            action = request.form.get('action', '')
            if action != (mode or 'note'):
                raise StaffReviewError('invalid_request')
            text = request.form.get('text', '')
            recommendation = request.form.get('recommendation', '')
            assignment = {}
            if action == 'reassign':
                selected_staff = request.form.get('responsible_staff_id', '')
                for key in ('responsible_staff_id', 'responsibility_version'):
                    value = request.form.get(key, '')
                    if (not value.isascii() or not value.isdecimal() or len(value) > 18
                            or int(value) < 1):
                        raise StaffReviewError('invalid_assignee')
                    assignment[key] = int(value)
            act_on_review(
                str(household_uuid), program, review_id, action, g.user['id'],
                *context, text=text, recommendation=recommendation, **assignment,
            )
        except StaffReviewError as failure:
            code = str(failure)
            if code in ('text_required', 'invalid_text', 'invalid_recommendation',
                        'invalid_assignee', 'same_assignee'):
                error = {
                    'text_required': 'Enter a brief reason before confirming reassignment.'
                    if mode == 'reassign' else 'Enter a brief note or explanation before saving.',
                    'invalid_text': 'Use up to 1,000 characters for a reassignment or cancellation reason.'
                    if mode in ('reassign', 'cancel') else 'Use up to 2,000 characters.',
                    'invalid_recommendation': 'Choose one of the two closing recommendations.',
                    'invalid_assignee': 'Choose an active, authorized staff account.',
                    'same_assignee': 'Choose a different staff member to reassign this review.',
                }[code]
            elif code == 'assignment_changed':
                error_status = 409
                selected_staff = ''
                error = (
                    'Responsible Staff changed while this form was open. '
                    'Check the current staff member shown here before choosing again.'
                )
            elif code == 'not_active':
                abort(409, description=(
                    'This review has already ended. Return to Staff Reviews '
                    'to see its saved outcome.'
                ))
            elif code == 'unauthorized':
                abort(403, description='Your account cannot update this review.')
            elif code == 'review_missing':
                abort(404, description='This staff review is unavailable for this household.')
            elif code in ('invalid_request', 'context_changed'):
                abort(400, description=(
                    'The selection changed or the form expired. '
                    'Return to Staff Reviews and try again.'
                ))
            else:
                abort(503, description=(
                    'The action could not be saved. Check the review before trying again.'
                ))
        else:
            flash({
                'note': 'Note saved.',
                'finish': (
                    f'Review finished. Find this record under {recommendation} '
                    'in the Review Queue.'
                ),
                'cancel': 'Review cancelled. No recommendation was made.',
                'reassign': 'Responsible Staff updated. The change is saved in assignment history.',
            }[action])
            return redirect(url_for(
                'assessments.review_record', household_uuid=household_uuid,
                review_program=program, review_id=review_id, **navigation['detail_parameters'],
            ), code=303)
    try:
        results = get_staff_review_detail(str(household_uuid))
    except AssessmentQueryError as failure:
        if str(failure) == 'household_assessment_missing':
            abort(404, description='This household assessment is unavailable in the selected batch.')
        abort(503, description='Staff reviews are unavailable. Please try again later.')
    record = next((row for row in results['review_history']
                   if row['id'] == review_id and row['program'] == program), None)
    if record is None:
        abort(404, description='This staff review is unavailable for this household.')
    if mode and record['status'] != 'In Review':
        abort(409, description=(
            'This review has already ended. Return to Staff Reviews '
            'to see its saved outcome.'
        ))
    assignable_staff = []
    if mode == 'reassign':
        try:
            assignable_staff = get_assignable_staff()
        except StaffReviewError:
            abort(503, description='Staff accounts are unavailable. Please try again later.')
    return render_template(
        'assessments/detail.html', results=results, tab='reviews', review_record=record,
        review_mode=mode, review_error=error, review_text=text[:2000],
        recommendation=recommendation, assignable_staff=assignable_staff,
        selected_staff=selected_staff, **navigation,
        role_label='System Administrator' if g.user['role'] == ADMIN_ROLE else 'LGU Staff',
        staff_username=g.user['username'], csrf_token=csrf_token(),
    ), (error_status if error else 200)
