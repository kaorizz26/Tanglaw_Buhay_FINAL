"""Developer-only reset of Citizen Account and Digital Dropbox TEST data in the rehearsal database.

Run it only through the rehearsal launcher, in an interactive terminal:

    powershell -File C:\\TanglawData\\rehearsal_config\\run_rehearsal.ps1 -Reset

It works only on tanglaw_buhay_rehearsal_0015 (localhost) with the rehearsal storage
folder, and stops if either differs or the rehearsal app is running. There are no
target options, and nothing in the Staff or Citizen interfaces can reach it.

Two scopes:
  Documents Only    document requests, uploaded files and their histories
  All Citizen Data  the above, plus Citizen Accounts, activation tokens and histories

Staff accounts, households, assessments, subsidy findings and Staff Reviews are never
touched. The database tables are emptied with TRUNCATE ... RESTRICT (no CASCADE).
TRUNCATE does not run the row-level append-only triggers, which stay in place and
enabled, exactly as in reset_staff_reviews.py.

Before anything is removed, the tables and the uploaded files are backed up and
verified. To recover, restore into the emptied tables and copy the files back:

    pg_restore --data-only --exit-on-error --single-transaction --no-owner ^
        --dbname=tanglaw_buhay_rehearsal_0015 <backup folder>\\tables.dump
    copy <backup folder>\\files\\* to the rehearsal storage folder

Files that no database record lists are reported but never deleted.
"""

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.reset_staff_reviews import ResetError, postgres_tools  # noqa: E402
from services.document_storage import STORAGE_KEY_PATTERN  # noqa: E402

EXPECTED_DATABASE = "tanglaw_buhay_rehearsal_0015"
EXPECTED_STORAGE = Path(r"C:\TanglawData\document_storage_rehearsal")
BACKUP_DIRECTORY = Path(r"C:\TanglawData\rehearsal_backups")
APP_ADDRESS = ("127.0.0.1", 5000)

# Children come before parents. Every table that points at another one in its own
# list is in the same list, so TRUNCATE ... RESTRICT needs no CASCADE.
DOCUMENT_TABLES = ("document_request_events", "document_files", "document_requests")
CITIZEN_TABLES = ("citizen_account_events", "citizen_account_tokens", "citizen_accounts")
SCOPES = {
    "documents": ("DOCUMENTS ONLY", DOCUMENT_TABLES),
    "all": ("ALL CITIZEN DATA", DOCUMENT_TABLES + CITIZEN_TABLES),
}
ALLOWED_SETTINGS = {"dbname", "host", "hostaddr", "port", "user", "password",
                    "connect_timeout", "sslmode", "application_name"}


# ---- Guards: only the approved rehearsal environment is accepted ----------------

def rehearsal_settings(database_url, environment):
    """Accept only an explicit localhost connection to the rehearsal database."""
    try:
        settings = conninfo_to_dict(database_url or "")
        host = settings.get("host")
        port = settings.get("port", "5432")
        address = settings.get("hostaddr", "::1" if host == "::1" else "127.0.0.1")
        if (set(settings) - ALLOWED_SETTINGS or settings.get("dbname") != EXPECTED_DATABASE
                or host not in ("localhost", "127.0.0.1", "::1")
                or address not in ("127.0.0.1", "::1")
                or (host != "localhost" and address != host)
                or not settings.get("user") or not port.isascii() or not port.isdecimal()
                or not 1 <= int(port) <= 65535
                or any(key.upper().startswith("PG") for key in environment)):
            raise ValueError
    except (psycopg.Error, ValueError, TypeError):
        raise ResetError(
            f"Run this only through the rehearsal launcher: DATABASE_URL must be an explicit "
            f"localhost {EXPECTED_DATABASE} connection, with no PG* environment settings."
        ) from None
    return {**settings, "hostaddr": address, "connect_timeout": "5",
            "application_name": "tanglaw_rehearsal_citizen_reset"}


def verify_target(connection, settings):
    """After connecting, confirm the server really is the rehearsal database."""
    row = connection.execute(
        "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
        "current_user, current_schema()",
    ).fetchone()
    if (row[0] != EXPECTED_DATABASE or not row[1]
            or not ipaddress.ip_address(row[1]).is_loopback
            or row[1] != settings["hostaddr"] or row[2] != int(settings.get("port", 5432))
            or row[3] != settings["user"] or row[4] != "public"):
        raise ResetError("Connected identity does not match the approved rehearsal database.")
    return f"{row[0]} on {row[1]}:{row[2]} as {row[3]} (schema public)"


def check_storage(configured):
    """Return the storage folder only if it is exactly the approved rehearsal folder."""
    if not configured:
        raise ResetError("DOCUMENT_STORAGE_DIR is not set; use the rehearsal launcher.")
    folder = Path(configured)
    if folder.is_symlink() or not folder.is_dir() or folder.resolve() != EXPECTED_STORAGE.resolve():
        raise ResetError("DOCUMENT_STORAGE_DIR is not the approved rehearsal storage folder.")
    return folder.resolve()


def refuse_if_app_is_running():
    """Stop if the rehearsal web app is up, so nobody uploads while records are removed."""
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(APP_ADDRESS) == 0:
            raise ResetError("The rehearsal app is running on port 5000. Stop it first.")


# ---- Counting and file inventory ----------------------------------------------

def _counts(connection, schema, tables):
    return {table: connection.execute(sql.SQL("SELECT count(*) FROM {}").format(
        sql.Identifier(schema, table))).fetchone()[0] for table in tables}


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _listed_files(connection, schema):
    """The files the database lists: (storage key, size, sha256), oldest first."""
    rows = connection.execute(sql.SQL(
        "SELECT storage_key, size_bytes, sha256 FROM {} ORDER BY id").format(
            sql.Identifier(schema, "document_files"))).fetchall()
    for key, _, _ in rows:
        if not STORAGE_KEY_PATTERN.fullmatch(key):
            raise ResetError("A stored file key has an unexpected format. Nothing was removed.")
    return rows


def _unlisted_files(storage_dir, listed):
    """Names in the storage folder that no database record lists; never deleted."""
    known = {key for key, _, _ in listed}
    return sorted(path.name for path in storage_dir.iterdir() if path.name not in known)


# ---- Backup --------------------------------------------------------------------

def _copy_files(listed, storage_dir, destination):
    """Copy every listed file that exists, check each copy, and describe the result."""
    destination.mkdir()
    described = []
    for key, size, expected_hash in listed:
        source = storage_dir / key
        if source.is_symlink() or not source.is_file():
            described.append({"key": key, "present": False})
            continue
        copy = destination / key
        shutil.copy2(source, copy)
        copy_hash = _sha256(copy)
        if copy_hash != _sha256(source):
            raise ResetError("A file copy did not match its original. Nothing was removed.")
        described.append({"key": key, "present": True, "size": copy.stat().st_size,
                          "sha256": copy_hash, "matches_database": copy_hash == expected_hash})
    return described


def _backup(connection, schema, settings, backup_root, scope, tables, listed, storage_dir, counts, tools):
    """Back up the tables and files from one snapshot; return the verified backup folder."""
    snapshot = connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_root / f"{stamp}_{scope}"
    partial = backup_root / f"{stamp}_{scope}.incomplete"
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    environment.update(PGHOST=settings["host"], PGHOSTADDR=settings["hostaddr"],
                       PGPORT=settings.get("port", "5432"), PGDATABASE=settings["dbname"],
                       PGUSER=settings["user"], PGPASSWORD=settings.get("password", ""),
                       PGCONNECT_TIMEOUT="5", PGSSLMODE=settings.get("sslmode", "prefer"))
    try:
        partial.mkdir(parents=True)
        dump = partial / "tables.dump"
        arguments = [tools[0], "--no-password", "--format=custom", "--data-only", "--no-owner",
                     "--no-privileges", "--strict-names", "--snapshot=" + snapshot,
                     "--file=" + str(dump)]
        arguments.extend("--table=" + schema + "." + table for table in tables)
        subprocess.run(arguments, env=environment, check=True, capture_output=True, timeout=120)
        listing = subprocess.run([tools[1], "--list", str(dump)], check=True,
                                 capture_output=True, text=True, timeout=30).stdout
        if set(re.findall(r"\bTABLE DATA (\S+) (\S+) ", listing)) != {(schema, t) for t in tables}:
            raise ResetError("Backup table verification failed. Nothing was removed.")
        # Read the whole archive without printing any of its contents.
        subprocess.run([tools[1], "--file=" + os.devnull, str(dump)], check=True,
                       capture_output=True, timeout=120)
        files = _copy_files(listed, storage_dir, partial / "files")
        manifest = {"database": settings["dbname"], "schema": schema, "scope": scope,
                    "created_at_utc": stamp, "counts": counts,
                    "tables_sha256": _sha256(dump), "files": files}
        (partial / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        partial.rename(final)
        return final
    except (OSError, subprocess.SubprocessError) as error:
        raise ResetError("Backup could not be completed and verified. Nothing was removed.") from error


# ---- The reset -----------------------------------------------------------------

def _remove_listed_files(listed, storage_dir):
    """Delete only the files the database listed; return how many were removed."""
    removed = 0
    for key, _, _ in listed:
        path = storage_dir / key
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed += 1
    return removed


def _reset_workflow(connection, schema, settings, backup_root, storage_dir, tools, scope, *,
                    confirm=input, output=print):
    """Preview, back up, confirm, empty the tables, then delete the listed files."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema) or scope not in SCOPES:
        raise ResetError("Invalid schema or scope.")
    scope_name, tables = SCOPES[scope]
    with connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        preview = _counts(connection, schema, tables)
    output("Records to remove: " + ", ".join(f"{table}={count}" for table, count in preview.items()))
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        names = sql.SQL(", ").join(sql.Identifier(schema, table) for table in tables)
        connection.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(names))
        counts = _counts(connection, schema, tables)
        if counts != preview:
            output("Counts changed before locking: " + str(counts))
        listed = _listed_files(connection, schema)
        unlisted = _unlisted_files(storage_dir, listed)
        output(f"Stored files listed by the database: {len(listed)}. "
               f"Files with no record (left alone): {len(unlisted)}.")
        backup = _backup(connection, schema, settings, backup_root, scope, tables,
                         listed, storage_dir, counts, tools)
        output(f"Verified backup of the tables and files: {backup}")
        phrase = f'RESET {settings["dbname"]} {scope_name}'
        if confirm(f'This permanently removes these test records and files. Type "{phrase}": ') != phrase:
            raise ResetError("Confirmation did not match. Nothing was removed; backup retained.")
        # Fixed, complete list of tables; no CASCADE, no identity reset, no trigger changes.
        connection.execute(sql.SQL("TRUNCATE TABLE {} CONTINUE IDENTITY RESTRICT").format(names))
        if any(_counts(connection, schema, tables).values()):
            raise ResetError("Reset verification failed; the transaction will roll back.")
    # Files are removed only after the database change is committed. A failure here
    # leaves harmless unlisted files, never records that point at missing files.
    removed = _remove_listed_files(listed, storage_dir)
    output("Removed: " + ", ".join(f"{table}={count}" for table, count in counts.items()))
    output(f"Stored files deleted: {removed}.")
    return backup, counts


def choose_scope(ask=input):
    """Ask which scope to reset; Documents Only is the default."""
    answer = ask("Reset [1] Documents Only (default) or [2] All Citizen Data? ").strip()
    if answer in ("", "1"):
        return "documents"
    if answer == "2":
        return "all"
    raise ResetError("Choose 1 or 2. Nothing was removed.")


def _is_real_terminal():
    """True only for a person at a console; on Windows the NUL device also passes isatty()."""
    if not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import ctypes
        mode = ctypes.c_ulong()
        handle = ctypes.windll.kernel32.GetStdHandle(-10)  # standard input
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def main():
    try:
        if len(sys.argv) != 1 or not _is_real_terminal():
            raise ResetError("Run this command in an interactive terminal without arguments.")
        settings = rehearsal_settings(os.environ.get("DATABASE_URL"), os.environ)
        storage_dir = check_storage(os.environ.get("DOCUMENT_STORAGE_DIR"))
        refuse_if_app_is_running()
        with psycopg.connect(**settings) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                identity = verify_target(connection, settings)
            print("Target: " + identity)
            tools = postgres_tools(connection.info.server_version)
            _reset_workflow(connection, "public", settings, BACKUP_DIRECTORY, storage_dir,
                            tools, choose_scope())
        return 0
    except (ResetError, psycopg.Error, OSError, subprocess.SubprocessError, EOFError,
            KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ResetError) else
              "Reset did not finish. Check the retained backup and the record counts before retrying.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
