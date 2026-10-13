"""Explicit, one-time local import runner; never logs source rows or credentials."""

from hashlib import file_digest
import csv
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from config import Config
from repositories import dataset_batches as repository
from services.cbms_import import import_csv_bundle
from services.cbms_preflight import validate_csv_bundle
from services.data_loader import EXCEL_FILES, REFERENCE_FILES

TABLES = {name: table for name, (table, _) in repository.SOURCE_TABLES.items()}
TABLES.update(repository.REFERENCE_TABLES)
EXPECTED = dict(zip(TABLES, (19304, 69350, 18282, 33, 705, 89, 214)))
FILES = {name: ROOT / 'data/raw' / f'{name}.csv' for name in EXCEL_FILES}
FILES.update({name: ROOT / 'data/reference' / filename
              for name, filename in REFERENCE_FILES.items()})
SCHEMA_COLUMNS = {
    'dataset_batches': ('id', 'survey_year', 'municipality', 'source_columns',
                        'source_provenance', 'validation_status', 'is_active', 'created_at'),
    'households': ('batch_id', 'uuid', 'source_row_number', 'responses'),
    'household_members': ('batch_id', 'uuid', 'line_number', 'source_row_number', 'responses'),
    'member_health': ('batch_id', 'uuid', 'section_l_line_number', 'source_row_number', 'responses'),
    'child_death_records': ('batch_id', 'source_row_number', 'uuid', 'responses'),
    'codebook_values': ('batch_id', 'source_row_number', 'responses'),
    'mpi_code_mappings': ('batch_id', 'source_row_number', 'responses'),
    'variable_definitions': ('batch_id', 'source_row_number', 'responses'),
    'staff_users': ('id', 'username', 'password_hash', 'role', 'is_active',
                    'session_version', 'created_at'),
}
TARGET = None
PHASE = 'configuration'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def emit(value):
    print(json.dumps(value), flush=True)


def file_hashes():
    paths = {**FILES, 'private_configuration': ROOT / '.env'}
    result = {}
    for name, path in paths.items():
        with path.open('rb') as handle:
            result[name] = file_digest(handle, 'sha256').hexdigest()
    return result


def connect_verified():
    """Pin loopback and public schema; all new connections begin read-only."""
    global TARGET
    settings = conninfo_to_dict(Config.DATABASE_URL or '')
    allowed = {'dbname', 'host', 'port', 'user', 'password', 'sslmode'}
    require(set(settings) <= allowed, 'unsupported_connection_parameters')
    require(settings.get('dbname') == 'tanglaw_buhay_dev', 'wrong_database_configuration')
    require(settings.get('host') in ('localhost', '127.0.0.1', '::1'), 'nonlocal_configuration')
    require(not any(os.getenv(name) for name in ('PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')),
            'service_or_options_environment')
    port = int(settings.get('port', '5432'))
    require(1 <= port <= 65535, 'invalid_port')
    address = '::1' if settings['host'] == '::1' else '127.0.0.1'
    settings.update(hostaddr=address, port=str(port), dbname='tanglaw_buhay_dev',
                    connect_timeout=10, application_name='tanglaw_controlled_import',
                    options='-c search_path=public -c default_transaction_read_only=on '
                            '-c lock_timeout=10000 -c statement_timeout=60000')
    connection = psycopg.connect(**settings, autocommit=True)
    try:
        identity = connection.execute(
            "SELECT current_database(), current_schema(), host(inet_server_addr()), "
            "inet_server_port(), (SELECT oid FROM pg_database WHERE datname=current_database()), "
            "(SELECT oid FROM pg_namespace WHERE nspname='public'), pg_is_in_recovery(), "
            "current_setting('transaction_read_only')"
        ).fetchone()
        require(identity[0] == 'tanglaw_buhay_dev' and identity[1] == 'public'
                and identity[2] == address and identity[3] == port
                and not identity[6] and identity[7] == 'on', 'connected_identity_mismatch')
        require(TARGET is None or TARGET == identity[:6], 'server_identity_changed')
        TARGET = identity[:6]
        return connection
    except Exception:
        connection.close()
        raise


def verify_schema(connection):
    """Verify installed migration structures using read-only catalogs."""
    for table, expected in SCHEMA_COLUMNS.items():
        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        require(tuple(row[0] for row in columns) == expected, 'migration_columns_mismatch')
    constraints = connection.execute(
        "SELECT con.contype, count(*), bool_and(con.convalidated) FROM pg_constraint con "
        "JOIN pg_class rel ON rel.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=rel.relnamespace "
        "WHERE ns.nspname='public' AND rel.relname=ANY(%s) GROUP BY con.contype",
        (list(SCHEMA_COLUMNS),),
    ).fetchall()
    catalog = {kind: (count, validated) for kind, count, validated in constraints}
    require(catalog.get('p') == (9, True) and catalog.get('f') == (7, True)
            and catalog.get('u') == (4, True), 'migration_constraints_mismatch')
    index = connection.execute(
        "SELECT ind.indisunique AND ind.indisvalid AND pg_get_expr(ind.indpred, ind.indrelid)='is_active' "
        "FROM pg_index ind JOIN pg_class idx ON idx.oid=ind.indexrelid "
        "JOIN pg_namespace ns ON ns.oid=idx.relnamespace "
        "WHERE ns.nspname='public' AND idx.relname='dataset_batches_one_active'"
    ).fetchone()
    require(index == (True,), 'active_batch_index_missing')
    return {'migration_0001_structure': True, 'migration_0002_structure': True,
            'expected_tables': 9, 'primary_keys': 9, 'foreign_keys': 7}


def table_counts(connection):
    return {name: connection.execute(sql.SQL('SELECT count(*) FROM public.{}').format(
        sql.Identifier(table))).fetchone()[0] for name, table in TABLES.items()}


def require_empty(connection):
    require(connection.execute('SELECT count(*) FROM public.dataset_batches').fetchone()[0] == 0,
            'existing_dataset_batch_stop')
    require(all(count == 0 for count in table_counts(connection).values()), 'existing_source_rows_stop')


def import_connection():
    """Reverify target and emptiness while holding the importer's table lock."""
    connection = connect_verified()
    try:
        verify_schema(connection)
        connection.execute('SET default_transaction_read_only=off')
        connection.autocommit = False
        repository.lock_dataset_imports(connection)
        require_empty(connection)
        return connection
    except Exception:
        connection.rollback()
        connection.close()
        raise


def staff_fingerprint(connection):
    return connection.execute(
        "SELECT count(*), md5(coalesce(string_agg(to_jsonb(s)::text, '' ORDER BY id), '')), "
        "count(*) FILTER (WHERE role='system_administrator' AND is_active) FROM public.staff_users s"
    ).fetchone()


def auth_smoke():
    """Check existing administrator session authorization without account writes."""
    from app import create_app
    from services.auth import start_session

    with connect_verified() as connection:
        account = connection.execute(
            "SELECT id, session_version FROM public.staff_users "
            "WHERE role='system_administrator' AND is_active ORDER BY id LIMIT 1"
        ).fetchone()
    require(account is not None, 'existing_admin_missing')
    app = create_app({'TESTING': True})
    client = app.test_client()
    with patch('services.auth.get_connection', side_effect=connect_verified):
        require(client.get('/login').status_code == 200, 'login_unavailable')
        require(client.get('/').status_code == 302, 'anonymous_guard_failed')
        with app.test_request_context():
            start_session({'id': account[0], 'session_version': account[1]})
            from flask import session
            session_state = dict(session)
        with client.session_transaction() as session:
            session.update(session_state)
        page = client.get('/')
        require(page.status_code == 200 and 'System Administrator' in page.text
                and 'Sign Out' in page.text, 'admin_authorization_failed')
        require(client.get('/logout').status_code == 200, 'logout_form_unavailable')
        require(client.post('/logout', data={}).status_code == 400, 'logout_csrf_guard_failed')
        require(client.get('/households').status_code == 410
                and client.get('/assessments').status_code == 410, 'retired_routes_guard_failed')
    return {'login_form': True, 'anonymous_guard': True, 'existing_admin_session': True,
            'logout_form_and_csrf': True, 'retired_routes': True}


def main():
    global PHASE
    require(sys.argv[1:] in (['--check'], ['--execute']), 'explicit_mode_required')
    hashes = file_hashes()
    PHASE = 'read_only_database_safeguards'
    with connect_verified() as connection:
        schema = verify_schema(connection)
        require_empty(connection)
        staff_before = staff_fingerprint(connection)
        require(staff_before[2] >= 1, 'existing_admin_missing')
    auth_before = auth_smoke()
    PHASE = 'read_only_preflight'
    report = validate_csv_bundle(ROOT / 'data')
    require(report['passed'], 'preflight_failed')
    counts = {**{name: stats['records'] for name, stats in report['datasets'].items()},
              **{name: stats['records'] for name, stats in report['references'].items()}}
    require(counts == EXPECTED, 'unexpected_source_counts')
    emit({'safeguards': 'PASS', 'database': TARGET[0], 'schema': TARGET[1],
          'server_address': TARGET[2], 'port': TARGET[3], 'schema_checks': schema,
          'existing_batches': 0, 'preflight': report, 'auth_before': auth_before})
    if sys.argv[1] == '--check':
        require(file_hashes() == hashes, 'source_or_configuration_changed')
        return
    with FILES['definitions'].open(encoding='utf-8-sig', newline='') as handle:
        supplements = [row['variable_name'] for row in csv.DictReader(handle)
                       if row['provenance'] == 'Synthetic Support']
    provenance = {
        'source_description': 'Owner-authorized anonymized PSA 2022 CBMS working dataset '
                              'for Pozorrubio, Pangasinan; historical project decision support.',
        'authorization': 'Project owner approved first controlled local tanglaw_buhay_dev import '
                         'on 2026-10-13.',
        'synthetic_supplements': {'provenance': 'Synthetic Support, as documented in finalized '
                                               'variable definitions', 'variable_names': supplements},
        'limitations': 'Original reference provenance and project pilot assumptions retained; '
                       'provisional Section L coding is not independently PSA-confirmed.',
    }
    require(file_hashes() == hashes, 'source_or_configuration_changed')
    PHASE = 'single_transactional_import'
    emit({'import': 'STARTING', 'attempts': 1})
    result = import_csv_bundle(ROOT / 'data', survey_year=2022, municipality='Pozorrubio',
                               source_provenance=provenance, connection_factory=import_connection)
    emit({'import_result': result})
    require(result['imported'], 'import_returned_error_no_retry')
    PHASE = 'read_only_post_import_verification'
    batch_id = result['batch_id']
    with connect_verified() as connection:
        batches = connection.execute(
            'SELECT id, survey_year, municipality, validation_status, is_active, '
            'source_provenance, source_columns FROM public.dataset_batches'
        ).fetchall()
        require(len(batches) == 1, 'unexpected_batch_count')
        batch = batches[0]
        require(batch[:5] == (batch_id, 2022, 'Pozorrubio', 'validated', True), 'batch_metadata_mismatch')
        stored_counts = table_counts(connection)
        require(stored_counts == EXPECTED, 'stored_counts_mismatch')
        require(batch[5]['declared'] == provenance, 'declared_provenance_mismatch')
        require(set(batch[5]['files']) == set(FILES) and set(batch[6]) == set(FILES), 'file_metadata_missing')
        for name, path in FILES.items():
            require(batch[5]['files'][name] == {'filename': path.name, 'sha256': hashes[name],
                                               'records': EXPECTED[name]}, 'file_provenance_mismatch')
            with path.open(encoding='utf-8-sig', newline='') as handle:
                require(batch[6][name] == next(csv.reader(handle)), 'source_columns_mismatch')
        orphans = {}
        for table in ('household_members', 'member_health', 'child_death_records'):
            orphans[table + '_to_households'] = connection.execute(sql.SQL(
                'SELECT count(*) FROM public.{} child WHERE NOT EXISTS '
                '(SELECT 1 FROM public.households parent WHERE parent.batch_id=child.batch_id '
                'AND parent.uuid=child.uuid)').format(sql.Identifier(table))).fetchone()[0]
        orphans['member_health_to_members'] = connection.execute(
            'SELECT count(*) FROM public.member_health child WHERE NOT EXISTS '
            '(SELECT 1 FROM public.household_members parent WHERE parent.batch_id=child.batch_id '
            'AND parent.uuid=child.uuid AND parent.line_number=child.section_l_line_number)'
        ).fetchone()[0]
        require(not any(orphans.values()), 'orphan_relationships_found')
        require(staff_fingerprint(connection) == staff_before, 'staff_accounts_changed')
    auth_after = auth_smoke()
    require(file_hashes() == hashes, 'source_or_configuration_changed')
    emit({'verification': 'PASS', 'database': TARGET[0], 'schema': TARGET[1], 'batch_id': batch_id,
          'validated_active_batches': 1, 'survey_year': 2022, 'municipality': 'Pozorrubio',
          'counts': {TABLES[name]: count for name, count in stored_counts.items()},
          'orphans': orphans, 'all_file_hashes_and_provenance_match': True,
          'staff_accounts_unchanged': True, 'auth_after': auth_after,
          'seven_csv_files_and_private_env_unchanged': True})


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error)
        safe_code = message if isinstance(error, RuntimeError) and len(message) < 80 and all(
            char.islower() or char == '_' for char in message
        ) else type(error).__name__
        emit({'status': 'FAIL_STOPPED_NO_RETRY', 'phase': PHASE,
              'safe_error_code': safe_code,
              'details': 'Failure details suppressed to protect source records and credentials.'})
        sys.exit(1)
