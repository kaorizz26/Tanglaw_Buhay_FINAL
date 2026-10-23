"""Prepare/check Run 3; execution needs a separately approved check fingerprint.

No MPI engine is called. --check uses one read-only snapshot. --execute repeats
the checks under locks and commits findings and designation atomically. No
migrations or repairs are attempted. Errors expose fixed diagnostics only.
"""

import argparse
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg import sql

from repositories import assessment_runs as repository, dataset_batches
from scripts.controlled_cbms_import_2022 import connect_verified
from services.active_batch_loader import _batch_metadata, _reconstruct_frame
from services.screening.metadata import PROGRAMS, STATUSES
from services.subsidy_priority import derive_subsidy_priority
from services.subsidy_screening import screen_households


VERSION = 'tanglaw-buhay-refined-subsidy-screening-v3'
HISTORICAL_VERSIONS = {
    1: 'tanglaw-buhay-approved-mpi-screening-v1',
    2: 'tanglaw-buhay-mpi-e01-unknown-v2',
}
HOUSEHOLDS = 19304
EXPECTED_ANY_REVIEW = 18439
EXPECTED_COUNTS = {
    '4Ps': (12147, 7150, 7), 'SLP': (12769, 2695, 3840),
    'SPISC': (6339, 12965, 0), 'TUPAD': (13455, 2693, 3156),
}
EXPECTED_PRIORITIES = {
    'Primary Priority': 760, 'Secondary Priority': 879,
    'No Subsidy Priority': 17665,
}
EXPECTED_TRANSITIONS = {
    'SLP': {(STATUSES[0], STATUSES[0]): 12769,
            (STATUSES[0], STATUSES[1]): 1,
            (STATUSES[1], STATUSES[1]): 2385,
            (STATUSES[2], STATUSES[1]): 309,
            (STATUSES[2], STATUSES[2]): 3840},
    'TUPAD': {(STATUSES[0], STATUSES[0]): 13455,
              (STATUSES[0], STATUSES[1]): 1,
              (STATUSES[0], STATUSES[2]): 3156,
              (STATUSES[1], STATUSES[1]): 2692},
}
CURRENT_STAGE = 'configuration'


class VerificationError(RuntimeError):
    """A fixed diagnostic without household data or connection details."""


def require(condition, code):
    if not condition:
        raise VerificationError(code)


def stage(name):
    """Report fixed processing labels only, never source values or identities."""
    global CURRENT_STAGE
    CURRENT_STAGE = name
    print(json.dumps({'stage': name}), flush=True)


def digest(value):
    """Hash the same complete JSON bytes without building one batch-sized string."""
    encoder = json.JSONEncoder(sort_keys=True, allow_nan=False, default=str)
    fingerprint = sha256()
    for chunk in encoder.iterencode(value):
        fingerprint.update(chunk.encode())
    return fingerprint.hexdigest()


def protected_tables(connection):
    """Include all source, staff and review tables without assuming review schema."""
    return [row[0] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
        "AND tablename NOT IN ('assessment_runs', 'household_assessments', "
        "'assessment_run_selections') ORDER BY tablename",
    )]


def preservation_snapshot(connection):
    """Fingerprint protected rows and complete historical runs, not their totals alone."""
    tables = [(name, '') for name in protected_tables(connection)]
    tables += [('assessment_runs', 'WHERE id IN (1, 2)'),
               ('household_assessments', 'WHERE run_id IN (1, 2)')]
    result = {}
    for name, predicate in tables:
        query = sql.SQL(
            "SELECT count(*), md5(coalesce(string_agg(row_hash, '' ORDER BY row_hash), '')) "
            "FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM {} t {}) rows"
        ).format(sql.Identifier(name), sql.SQL(predicate))
        result[name] = connection.execute(query).fetchone()
    return result


def preflight(connection):
    """Require the fixed validated source, designated Run 2 and no prior attempt."""
    metadata = _batch_metadata(dataset_batches.find_active_batches(connection))
    require(metadata['batch_id'] == 1 and metadata['survey_year'] == 2022
            and metadata['municipality'] == 'Pozorrubio', 'unexpected_source_batch')
    runs = connection.execute(
        'SELECT id FROM assessment_runs WHERE batch_id=1 OR id=3 '
        'OR calculation_version=%s ORDER BY id', (VERSION,),
    ).fetchall()
    require(runs == [(1,), (2,)], 'existing_or_partial_run_3_attempt')
    for run_id, version in HISTORICAL_VERSIONS.items():
        run = repository.find_run(connection, run_id)
        require(run is not None and run['status'] == 'completed'
                and run['batch_id'] == 1 and run['calculation_version'] == version
                and run['expected_household_count'] == HOUSEHOLDS,
                'historical_run_metadata_mismatch')
        counts = connection.execute(
            'SELECT count(*) FROM household_assessments WHERE run_id=%s', (run_id,),
        ).fetchone()[0]
        require(counts == HOUSEHOLDS, 'historical_run_incomplete')
    selected = repository.find_designated_run(connection, 1)
    require(selected is not None and selected['run_id'] == 2, 'run_2_not_designated')
    # PostgreSQL sequences are not rolled back. A consumed ID needs manual review,
    # never an automatic reset/retry that might conceal an earlier failed attempt.
    sequence = connection.execute(
        "SELECT pg_get_serial_sequence('assessment_runs', 'id')",
    ).fetchone()[0]
    require(sequence is not None, 'run_sequence_missing')
    position = connection.execute(sql.SQL('SELECT last_value, is_called FROM {}').format(
        sql.Identifier(*sequence.split('.')),
    )).fetchone()
    require(position == (2, True), 'run_sequence_requires_review')
    return metadata


def build_findings(datasets, baseline):
    """Calculate screening once and replace only program payloads by original UUID."""
    households = datasets['household']
    require(len(households) == HOUSEHOLDS and not households['uuid'].duplicated().any()
            and set(households['uuid']) == set(baseline), 'household_coverage_mismatch')
    stage('screening')
    screening = screen_households(datasets, survey_year=2022)
    require(len(screening) == HOUSEHOLDS * len(PROGRAMS)
            and not screening.duplicated(['household_uuid', 'program_code']).any(),
            'screening_coverage_mismatch')
    stage('assemble_findings')
    # Old program evidence is replaced, so copying it would only inflate the peak.
    # Copy MPI separately so even nested changes cannot mutate the saved baseline.
    findings = {
        uuid: {**previous, 'mpi': deepcopy(previous['mpi']), 'programs': {}}
        for uuid, previous in baseline.items()
    }
    for values in screening.itertuples(index=False, name=None):
        program = dict(zip(screening.columns, values))
        uuid = program['household_uuid']
        require(uuid in findings, 'screening_household_mismatch')
        findings[uuid]['programs'][program['program_code']] = program
    del screening
    stage('verify_complete_findings')
    verify_findings(findings, baseline)
    return findings


def verify_findings(findings, baseline):
    """Require complete MPI/unchanged programs and all approved impact aggregates."""
    require(len(findings) == HOUSEHOLDS and set(findings) == set(baseline),
            'findings_coverage_mismatch')
    counts = {code: Counter() for code in PROGRAMS}
    transitions = {code: Counter() for code in EXPECTED_TRANSITIONS}
    priorities = Counter()
    any_review = 0
    for uuid, finding in findings.items():
        previous = baseline[uuid]
        require(finding['household_uuid'] == uuid and finding['survey_year'] == 2022
                and finding['mpi'] == previous['mpi'], 'saved_mpi_or_identity_changed')
        programs = finding['programs']
        require(set(programs) == set(PROGRAMS), 'program_coverage_mismatch')
        for code, program in programs.items():
            require(program['household_uuid'] == uuid and program['program_code'] == code
                    and program['survey_year'] == 2022
                    and program['screening_status'] in STATUSES, 'invalid_program_identity')
            if code in ('4Ps', 'SPISC'):
                require(program == previous['programs'][code], 'unchanged_program_mismatch')
            counts[code][program['screening_status']] += 1
            if code in transitions:
                transitions[code][(previous['programs'][code]['screening_status'],
                                   program['screening_status'])] += 1
        statuses = {code: item['screening_status'] for code, item in programs.items()}
        priorities[derive_subsidy_priority(finding['mpi']['vulnerability_score'], statuses)] += 1
        any_review += 'For Review' in statuses.values()
        repository._serialize_findings(finding, uuid, 2022)
    require(all(tuple(counts[code][status] for status in STATUSES) == expected
                for code, expected in EXPECTED_COUNTS.items()), 'status_counts_mismatch')
    require(transitions == EXPECTED_TRANSITIONS, 'status_transitions_mismatch')
    require(priorities == EXPECTED_PRIORITIES and any_review == EXPECTED_ANY_REVIEW,
            'priority_counts_mismatch')


def prepare(connection):
    """Load source and saved MPI in the caller's single stable transaction."""
    stage('preflight')
    metadata = preflight(connection)
    stage('snapshot_protected_data')
    snapshot = preservation_snapshot(connection)
    stage('load_run_2_findings')
    baseline = {item['household_uuid']: item for item in
                repository.iter_household_assessments(connection, 2)}
    require(len(baseline) == HOUSEHOLDS, 'run_2_coverage_mismatch')
    datasets = {}
    for name in ('household', 'section_a_e_indiv', 'section_l02_to_l41'):
        stage('load_' + name)
        datasets[name] = _reconstruct_frame(connection, metadata, name)
    findings = build_findings(datasets, baseline)
    # Complete comparisons have passed; these large inputs are no longer needed.
    del datasets, baseline
    paths = [Path(__file__), ROOT / 'services/subsidy_screening.py',
             ROOT / 'services/subsidy_priority.py',
             ROOT / 'repositories/assessment_runs.py',
             ROOT / 'services/active_batch_loader.py',
             *sorted((ROOT / 'services/screening').glob('*.py'))]
    stage('fingerprint_findings')
    provenance = {
        'calculation_version': VERSION, 'mpi_source_run_id': 2,
        'mpi_handling': 'Complete saved MPI copied unchanged; no recalculation',
        'source_batch_provenance': metadata['source_provenance'],
        'engine_sha256': {path.name: sha256(path.read_bytes()).hexdigest() for path in paths},
        'findings_sha256': digest(findings), 'protected_state_sha256': digest(snapshot),
        'verification': 'Approved aggregate counts/transitions plus separately approved full-payload fingerprint',
    }
    stage('verify_protected_data')
    require(preservation_snapshot(connection) == snapshot, 'protected_state_changed')
    return findings, provenance, snapshot


def persist_version(connection, findings, provenance, snapshot):
    """Verify round trips and preservation before designation; any failure rolls back."""
    with connection.transaction():
        run_id = repository.create_run(
            connection, 1, calculation_version=VERSION, calculation_provenance=provenance,
        )
        require(run_id == 3, 'unexpected_new_run_id')
        repository.store_household_assessments(connection, run_id, findings)
        repository.complete_run(connection, run_id)
        # Compare complete payloads one at a time; retain only unmatched keys.
        remaining = set(findings)
        for stored in repository.iter_household_assessments(connection, run_id):
            uuid = stored['household_uuid']
            require(uuid in remaining and stored == findings[uuid],
                    'stored_complete_findings_mismatch')
            remaining.remove(uuid)
        require(not remaining, 'stored_complete_findings_mismatch')
        run = repository.find_run(connection, run_id)
        require(run['calculation_version'] == VERSION
                and run['calculation_provenance'] == provenance
                and run['expected_household_count'] == len(findings),
                'stored_run_metadata_mismatch')
        require(preservation_snapshot(connection) == snapshot, 'protected_state_changed')
        repository.designate_run(connection, 1, run_id)
        selected = repository.find_designated_run(connection, 1)
        require(selected is not None and selected['run_id'] == 3, 'designation_mismatch')
        require(preservation_snapshot(connection) == snapshot, 'protected_state_changed')
        return run_id


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--execute', action='store_true')
    parser.add_argument('--approved-sha256', help='Separately approved --check fingerprint')
    args = parser.parse_args(arguments)
    try:
        stage('configuration')
        require(not args.execute or (args.approved_sha256 is not None
                and len(args.approved_sha256) == 64), 'approved_check_fingerprint_required')
        stage('connect_read_only')
        with connect_verified() as connection, connection.transaction():
            if args.check:
                connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            else:
                # Read committed sees the latest state after all locks are acquired.
                connection.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ WRITE')
                connection.execute('SELECT pg_advisory_xact_lock(6042022)')
                for table in [*protected_tables(connection), 'assessment_runs',
                              'household_assessments', 'assessment_run_selections']:
                    connection.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(
                        sql.Identifier(table),
                    ))
            findings, provenance, snapshot = prepare(connection)
            stage('fingerprint_approval')
            approval = digest(provenance)
            if args.execute:
                require(approval == args.approved_sha256, 'approved_check_changed')
                stage('persist_and_verify')
                persist_version(connection, findings, provenance, snapshot)
            stage('finish_transaction')
        print(json.dumps({'mode': 'execute' if args.execute else 'check', 'result': 'PASS',
                          'version': VERSION, 'households': HOUSEHOLDS,
                          'subsidy_results': HOUSEHOLDS * 4, 'approval_sha256': approval}))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print(json.dumps({'result': 'FAIL', 'safe_error_code': code, 'stage': CURRENT_STAGE,
                          'action': 'Stopped without automatic repair or retry'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
