"""Validated queue filters, bounded read-only queries and staff presentation."""

from dataclasses import dataclass

from werkzeug.datastructures import MultiDict

from db import get_connection
from repositories import dataset_batches, staff_reviews
from services.assessment_filters import (
    AssessmentFilters, InvalidAssessmentFilters, PROGRAM_ORDER, normalize_filters,
)
from services.assessment_queries import AssessmentQueryError, _read_context
from services.prioritization import (
    PAGE_SIZE, _head_name, _household_reference_id, _page_numbers, _source_text,
)


STATUS_OPTIONS = (
    ('in-review', 'In Review'),
    ('follow-up', 'For LGU Follow-Up'),
    ('no-action', 'No Further Action'),
)
STATUS_EXPLANATIONS = {
    'in-review': 'Staff have started these reviews but have not finished them yet.',
    'follow-up': 'Staff have finished these reviews and recommend further consideration by the LGU.',
    'no-action': 'Staff have finished these reviews and recommend no additional action at this time.',
}
RETURN_KEYS = ('status', 'q', 'barangay', 'program', 'page', 'scope', 'sort')


@dataclass(frozen=True)
class QueueFilters:
    status: str = 'in-review'
    search: str = ''
    barangay: str = ''
    programs: tuple[str, ...] = ()
    scope: str = 'all'
    sort: str = ''

    @property
    def date_order(self):
        return self.sort or ('asc' if self.status == 'in-review' else 'desc')

    @property
    def active(self):
        return bool(self.search or self.barangay or self.programs)

    @property
    def parameters(self):
        return {'status': self.status, 'scope': self.scope,
                **({'sort': self.sort} if self.sort else {}), **AssessmentFilters(
            search=self.search, barangay=self.barangay, programs=self.programs,
        ).parameters}


def normalize_queue_filters(filters=None):
    filters = QueueFilters() if filters is None else filters
    if (not isinstance(filters, QueueFilters) or not isinstance(filters.status, str)
            or filters.status not in dict(STATUS_OPTIONS)
            or filters.scope not in ('all', 'mine') or filters.sort not in ('', 'asc', 'desc')):
        raise InvalidAssessmentFilters('invalid_review_queue_filters')
    normalized = normalize_filters(AssessmentFilters(
        search=filters.search, barangay=filters.barangay, programs=filters.programs,
    ))
    return QueueFilters(filters.status, normalized.search, normalized.barangay, normalized.programs,
                        filters.scope, filters.sort)


def parse_queue_arguments(arguments, *, default_scope='all'):
    values = {}
    for key, default in (('status', 'in-review'), ('q', ''), ('barangay', ''), ('page', '1'),
                         ('scope', default_scope), ('sort', '')):
        supplied = arguments.getlist(key)
        if len(supplied) > 1:
            raise InvalidAssessmentFilters('invalid_review_queue_filters')
        values[key] = supplied[0] if supplied else default
    page = values['page']
    if not page.isascii() or not page.isdecimal() or len(page) > 9 or int(page) < 1:
        raise InvalidAssessmentFilters('invalid_review_queue_page')
    filters = normalize_queue_filters(QueueFilters(
        status=values['status'], search=values['q'], barangay=values['barangay'],
        programs=tuple(arguments.getlist('program')),
        scope=values['scope'], sort=values['sort'],
    ))
    return int(page), filters


def queue_return_parameters(page, filters):
    return {'from_queue': '1', 'queue_page': page, **{
        'queue_' + key: value for key, value in filters.parameters.items()
    }}


def parse_queue_return(arguments):
    """Accept structured state only; never accept an arbitrary redirect URL."""
    supplied = arguments.getlist('from_queue')
    queue_keys = [key for key in arguments if key.startswith('queue_')]
    if not supplied and not queue_keys:
        return None
    if supplied != ['1'] or any(key[6:] not in RETURN_KEYS for key in queue_keys):
        raise InvalidAssessmentFilters('invalid_review_queue_return')
    values = MultiDict((key, value) for key in RETURN_KEYS
                       for value in arguments.getlist('queue_' + key))
    return parse_queue_arguments(values)


def get_review_queue_page(page, filters=None, *, staff_id=None, connection_factory=get_connection):
    filters = normalize_queue_filters(filters)
    if type(page) is not int or page < 1:
        raise AssessmentQueryError('invalid_assessment_pagination')
    if filters.scope == 'mine' and (type(staff_id) is not int or staff_id < 1):
        raise InvalidAssessmentFilters('invalid_review_queue_staff')
    with _read_context(connection_factory) as (connection, context):
        total = staff_reviews.count_queue(
            connection, context['batch_id'], context['run_id'], filters, staff_id=staff_id,
        )
        page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if page > page_count:
            return None
        items = staff_reviews.list_queue(
            connection, context['batch_id'], context['run_id'], filters,
            limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, staff_id=staff_id,
        )
        identities = dataset_batches.find_household_identities(
            connection, context['batch_id'], list({row['household_uuid'] for row in items}),
        )
        indexed = {row['household_uuid']: row for row in identities}
        if set(indexed) != {row['household_uuid'] for row in items}:
            raise AssessmentQueryError('household_identity_missing')
        rows = []
        for item in items:
            identity = indexed[item['household_uuid']]
            rows.append({
                **item,
                'household_reference_id': _household_reference_id(identity['source_row_number']),
                'head_name': _head_name(identity),
                'barangay': _source_text(identity['barangay']) or 'Not recorded',
            })
        barangays = dataset_batches.list_barangays(connection, context['batch_id'])
    return {
        'context': context, 'items': rows, 'total': total, 'page': page,
        'page_count': page_count, 'page_numbers': _page_numbers(page, page_count),
        'first_record': (page - 1) * PAGE_SIZE + 1 if rows else 0,
        'last_record': (page - 1) * PAGE_SIZE + len(rows) if rows else 0,
        'filters': filters, 'filter_parameters': filters.parameters, 'barangays': barangays,
        'tab_filter_parameters': {key: value for key, value in filters.parameters.items()
                                  if key != 'status'},
        'scope_filter_parameters': {key: value for key, value in filters.parameters.items()
                                    if key != 'scope'},
        'program_options': PROGRAM_ORDER, 'status_options': STATUS_OPTIONS,
        'status_label': dict(STATUS_OPTIONS)[filters.status],
        'status_explanation': STATUS_EXPLANATIONS[filters.status],
        'status_explanations': STATUS_EXPLANATIONS,
        'status_style': {'in-review': 'active', 'follow-up': 'follow-up',
                         'no-action': 'no-action'}[filters.status],
        'return_parameters': queue_return_parameters(page, filters),
    }
