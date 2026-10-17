"""Interactive reset of disposable local development reviews, with verified backup.

Never use this utility for operational LGU history. The command-line entry point
accepts only localhost/tanglaw_buhay_dev/public; there are no target override flags.
"""

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import Config  # noqa: E402


TABLES = ('staff_review_notes', 'staff_review_reassignments',
          'staff_review_recommendation_changes', 'staff_reviews')
BACKUP_DIRECTORY = ROOT / 'notes' / 'staff_review_backups'


class ResetError(RuntimeError):
    """A safe diagnostic which contains no credentials or review content."""


def local_settings(database_url, environment):
    """Reject implicit libpq targets, remote hosts and connection redirection."""
    allowed = {'dbname', 'host', 'hostaddr', 'port', 'user', 'password',
               'connect_timeout', 'sslmode', 'application_name'}
    try:
        settings = conninfo_to_dict(database_url or '')
        host = settings.get('host')
        port = settings.get('port', '5432')
        address = settings.get('hostaddr', '::1' if host == '::1' else '127.0.0.1')
        if (set(settings) - allowed or settings.get('dbname') != 'tanglaw_buhay_dev'
                or host not in ('localhost', '127.0.0.1', '::1')
                or address not in ('127.0.0.1', '::1')
                or (host != 'localhost' and address != host)
                or not settings.get('user') or not port.isascii() or not port.isdecimal()
                or not 1 <= int(port) <= 65535
                or any(key.upper().startswith('PG') for key in environment)):
            raise ValueError
    except (psycopg.Error, ValueError, TypeError):
        raise ResetError('Use an explicit localhost tanglaw_buhay_dev DATABASE_URL; '
                         'remove ambiguous PG* environment settings.') from None
    return {**settings, 'hostaddr': address, 'connect_timeout': '5',
            'application_name': 'tanglaw_disposable_review_reset'}


def verify_target(connection, settings):
    row = connection.execute(
        'SELECT current_database(), inet_server_addr()::text, inet_server_port(), '
        'current_user, current_schema()',
    ).fetchone()
    if (row[0] != 'tanglaw_buhay_dev' or not row[1]
            or not ipaddress.ip_address(row[1]).is_loopback
            or row[1] != settings['hostaddr'] or row[2] != int(settings.get('port', 5432))
            or row[3] != settings['user'] or row[4] != 'public'):
        raise ResetError('Connected identity does not match the approved local development target.')
    return f'{row[0]} on {row[1]}:{row[2]} as {row[3]} (schema public)'


def postgres_tools(server_version):
    dump = shutil.which('pg_dump')
    if dump is None:
        candidate = Path(r'C:\Program Files\PostgreSQL') / str(server_version // 10000) / 'bin' / 'pg_dump.exe'
        dump = str(candidate) if candidate.is_file() else None
    restore = Path(dump).with_name('pg_restore.exe' if os.name == 'nt' else 'pg_restore') if dump else None
    if not dump or not restore.is_file():
        raise ResetError('Install matching PostgreSQL pg_dump and pg_restore tools before resetting.')
    version = subprocess.run([dump, '--version'], check=True, capture_output=True,
                             text=True, timeout=10).stdout
    match = re.search(r'PostgreSQL\) (\d+)', version)
    if not match or int(match[1]) != server_version // 10000:
        raise ResetError('pg_dump must match the connected PostgreSQL major version.')
    return dump, str(restore)


def _counts(connection, schema):
    return {table: connection.execute(sql.SQL('SELECT count(*) FROM {}').format(
        sql.Identifier(schema, table),
    )).fetchone()[0] for table in TABLES}


def _backup(connection, schema, settings, directory, counts, tools):
    """Back up one exported snapshot while the caller blocks review writes."""
    snapshot = connection.execute('SELECT pg_export_snapshot()').fetchone()[0]
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:10]
    partial = directory / (stem + '.incomplete')
    archive = directory / (stem + '.dump')
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith('PG')}
    environment.update(PGHOST=settings['host'], PGHOSTADDR=settings['hostaddr'],
                       PGPORT=settings.get('port', '5432'), PGDATABASE=settings['dbname'],
                       PGUSER=settings['user'], PGPASSWORD=settings.get('password', ''),
                       PGCONNECT_TIMEOUT='5', PGSSLMODE=settings.get('sslmode', 'prefer'))
    arguments = [tools[0], '--no-password', '--format=custom', '--data-only',
                 '--no-owner', '--no-privileges', '--strict-names', '--snapshot=' + snapshot,
                 '--file=' + str(partial)]
    arguments.extend('--table=' + schema + '.' + table for table in TABLES)
    try:
        subprocess.run(arguments, env=environment, check=True, capture_output=True, timeout=120)
        listing = subprocess.run([tools[1], '--list', str(partial)], check=True,
                                 capture_output=True, text=True, timeout=30).stdout
        backed_up = set(re.findall(r'\bTABLE DATA (\S+) (\S+) ', listing))
        if backed_up != {(schema, table) for table in TABLES} or partial.stat().st_size == 0:
            raise ResetError('Backup table verification failed. Nothing was removed.')
        # Read/decompress the entire archive, without printing private SQL to the terminal.
        subprocess.run([tools[1], '--file=' + os.devnull, str(partial)], check=True,
                       capture_output=True, timeout=120)
        digest = hashlib.sha256(partial.read_bytes()).hexdigest()
        metadata = {'database': settings['dbname'], 'schema': schema,
                    'counts': counts, 'sha256': digest, 'created_at_utc': stem.split('_')[0]}
        archive.with_suffix('.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        partial.rename(archive)
        return archive
    except (OSError, subprocess.SubprocessError) as error:
        raise ResetError('Backup could not be completed and verified. Nothing was removed.') from error


def _reset_workflow(connection, schema, settings, directory, tools, *,
                    confirm=input, output=print):
    """Internal workflow; tests supply their existing isolated synthetic schema."""
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', schema):
        raise ResetError('Invalid review schema.')
    with connection.transaction():
        connection.execute('SET TRANSACTION READ ONLY')
        preview = _counts(connection, schema)
    output('Records to remove: ' + ', '.join(f'{table}={count}' for table, count in preview.items()))
    output('Only disposable development reviews may be reset. Never reset operational LGU history.')
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        names = sql.SQL(', ').join(sql.Identifier(schema, table) for table in TABLES)
        connection.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(names))
        counts = _counts(connection, schema)
        if counts != preview:
            output('Counts changed before locking: ' + str(counts))
        archive = _backup(connection, schema, settings, directory, counts, tools)
        output(f'Verified recoverable backup: {archive}')
        phrase = f'RESET {settings["dbname"]} DISPOSABLE REVIEWS ONLY'
        if confirm(f'This permanently removes these review records. Type "{phrase}": ') != phrase:
            raise ResetError('Confirmation did not match. No review records were removed; backup retained.')
        # Fixed, complete FK dependency set; no CASCADE, identity reset or trigger changes.
        # TRUNCATE leaves the append-only UPDATE/DELETE trigger and schema intact.
        connection.execute(sql.SQL('TRUNCATE TABLE {} CONTINUE IDENTITY RESTRICT').format(names))
        if any(_counts(connection, schema).values()):
            raise ResetError('Reset verification failed; the transaction will roll back.')
    output('Removed: ' + ', '.join(f'{table}={count}' for table, count in counts.items()))
    return archive, counts


def main():
    try:
        if len(sys.argv) != 1 or not sys.stdin.isatty():
            raise ResetError('Run this command in an interactive terminal without arguments.')
        settings = local_settings(Config.DATABASE_URL, os.environ)
        with psycopg.connect(**settings) as connection:
            with connection.transaction():
                connection.execute('SET TRANSACTION READ ONLY')
                identity = verify_target(connection, settings)
            print('Target: ' + identity)
            tools = postgres_tools(connection.info.server_version)
            _reset_workflow(connection, 'public', settings, BACKUP_DIRECTORY, tools)
        return 0
    except (ResetError, psycopg.Error, OSError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ResetError) else
              'Reset did not finish. Check the retained backup and review counts before retrying.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
