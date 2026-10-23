"""Compare screening once against designated Run 2; emit aggregates only.

Use the validated database batch and saved MPI scores. All database work shares
one repeatable-read, read-only snapshot. No assessments or reports are persisted.
"""

from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repositories import assessment_runs, dataset_batches
from scripts.controlled_cbms_import_2022 import connect_verified
from services.active_batch_loader import _batch_metadata, _reconstruct_frame
from services.assessment_queries import _read_context
from services.screening.metadata import PROGRAMS, STATUSES
from services.subsidy_priority import derive_subsidy_priority
from services.subsidy_screening import screen_households


def require(condition, message):
    """Fail with a fixed diagnostic, never private source values."""
    if not condition:
        raise RuntimeError(message)


def canonical(value):
    """Compare complete payloads, treating JSON arrays and tuples alike."""
    return json.dumps(value, sort_keys=True)


def verify():
    with _read_context(connect_verified) as (connection, context):
        require(context['run_id'] == 2, 'designated_run_is_not_2')
        require(connection.execute('SHOW transaction_read_only').fetchone()[0]
                == 'on', 'read_only_required')
        metadata = _batch_metadata(dataset_batches.find_active_batches(connection))
        require(metadata['batch_id'] == context['batch_id']
                and metadata['survey_year'] == 2022, 'batch_context_mismatch')
        # Reuse loader validation/reconstruction inside the same snapshot as Run 2.
        datasets = {name: _reconstruct_frame(connection, metadata, name) for name in
                    ('household', 'section_a_e_indiv', 'section_l02_to_l41')}
        require(len(datasets['household']) == 19304, 'unexpected_household_count')
        old = {item['household_uuid']: item for item in
               assessment_runs.iter_household_assessments(connection, 2)}
        require(len(old) == 19304, 'incomplete_run_2')
        print('Validated Run 2 and 2022 source batch; calculating screening once.', flush=True)
        latest = screen_households(datasets, survey_year=2022)
        proposed = {}
        for row in latest.to_dict('records'):
            proposed.setdefault(row['household_uuid'], {})[row['program_code']] = row
        require(set(old) == set(proposed), 'household_identity_mismatch')

        totals = {code: {'old': Counter(), 'new': Counter(),
                         'transitions': Counter(), 'payload_changes': 0}
                  for code in PROGRAMS}
        priorities = Counter()
        score_bands = Counter()
        any_review = Counter()
        pathways = {'old': Counter(), 'new': Counter()}
        warnings = {code: {'old': Counter(), 'new': Counter()} for code in ('SLP', 'TUPAD')}
        for uuid, saved in old.items():
            before, after = saved['programs'], proposed[uuid]
            require(set(after) == set(PROGRAMS), 'incomplete_program_results')
            for code in PROGRAMS:
                old_status = before[code]['screening_status']
                new_status = after[code]['screening_status']
                require(old_status in STATUSES and new_status in STATUSES, 'invalid_status')
                totals[code]['old'][old_status] += 1
                totals[code]['new'][new_status] += 1
                totals[code]['transitions'][(old_status, new_status)] += 1
                totals[code]['payload_changes'] += canonical(before[code]) != canonical(after[code])
            score = saved['mpi']['vulnerability_score']
            band = '>25' if score > 25 else '20-25' if score >= 20 else '<20'
            score_bands[band] += 1
            old_statuses = {code: before[code]['screening_status'] for code in PROGRAMS}
            new_statuses = {code: after[code]['screening_status'] for code in PROGRAMS}
            priorities[(derive_subsidy_priority(score, old_statuses),
                        derive_subsidy_priority(score, new_statuses))] += 1
            any_review[(any(s == 'For Review' for s in old_statuses.values()),
                        any(s == 'For Review' for s in new_statuses.values()))] += 1
            for label, programs in (('old', before), ('new', after)):
                paths = {item['pathway'] for item in programs['SLP']['supporting_evidence']
                         if item.get('pathway') in ('EF', 'MD')}
                pathways[label]['+'.join(sorted(paths)) or 'none'] += 1
                for code in warnings:
                    warnings[code][label].update(item['code'] for item in
                                                 programs[code]['data_quality_warnings'])

        report = {
            'households': len(old), 'batch': metadata['batch_id'], 'run': 2,
            'score_bands': dict(score_bands), 'programs': {},
            'priority_transitions': [dict(old=a, new=b, count=n)
                                     for (a, b), n in sorted(priorities.items())],
            'any_review_transitions': [dict(old=a, new=b, count=n)
                                       for (a, b), n in sorted(any_review.items())],
            'slp_household_pathways': pathways, 'warning_occurrences': warnings,
        }
        for code, values in totals.items():
            report['programs'][code] = {
                **{key: values[key] for key in ('old', 'new', 'payload_changes')},
                'transitions': [dict(old=a, new=b, count=n) for (a, b), n in
                                sorted(values['transitions'].items())],
            }
        print(json.dumps(report, indent=2))
        require(all(totals[code]['payload_changes'] == 0 for code in ('4Ps', 'SPISC')),
                'unchanged_program_payload_mismatch')


if __name__ == '__main__':
    try:
        verify()
    except Exception:
        # Suppress database errors/tracebacks that could include source values.
        print('Impact verification failed; inspect validation logic without exposing private records.')
        raise SystemExit(1) from None
