"""Isolated PostgreSQL checks for reference loading and metadata audit."""

import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from werkzeug.datastructures import FileStorage, MultiDict
from werkzeug.security import generate_password_hash

from app import create_app
from repositories import import_validation_events
from cbms_csv_fixtures import synthetic_csv_frames, write_csv_fixture
from services.cbms_import import DuplicateImportBundle, stage_validated_snapshot
from services.cbms_preflight import load_csv_bundle
from services.data_loader import REFERENCE_FILES
from services.import_references import load_import_references
from services.import_validation import validate_admin_upload
from services.import_assessment_preparation import (
    AssessmentPreparationError, list_import_batches, prepare_import_batch,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_URL = os.getenv("TANGLAW_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for isolated database checks.")
class ImportValidationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings = conninfo_to_dict(TEST_URL)
        if (settings.get("dbname") != "tanglaw_buhay_test"
                or settings.get("host") not in ("localhost", "127.0.0.1", "::1")
                or settings.get("hostaddr", "127.0.0.1") not in ("127.0.0.1", "::1")
                or "service" in settings):
            raise ValueError("Synthetic checks require the local test database.")
        cls.schema = f"import_check_{uuid4().hex}"
        cls.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={cls.schema}",
        )
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)),
            )
        try:
            with psycopg.connect(cls.url) as connection:
                for name in (
                    "0001_database_foundation.sql",
                    "0002_staff_authentication.sql",
                    "0003_assessment_persistence.sql",
                    "0004_assessment_run_selection.sql",
                    "0013_import_validation_audit.sql",
                ):
                    connection.execute((ROOT / "migrations" / name).read_text())
                cls.actor_id = connection.execute(
                    """INSERT INTO staff_users (username, password_hash, role)
                       VALUES ('synthetic_admin', %s, 'system_administrator')
                       RETURNING id""",
                    (generate_password_hash("synthetic password", method="scrypt"),),
                ).fetchone()[0]
                _, reference_frames = synthetic_csv_frames()
                columns = {}
                files = {}
                for name, filename in REFERENCE_FILES.items():
                    columns[name] = list(reference_frames[name].columns)
                    files[name] = {"filename": filename,
                                   "records": len(reference_frames[name]),
                                   "sha256": "a" * 64}
                cls.batch_id = connection.execute(
                    """INSERT INTO dataset_batches
                       (survey_year, municipality, source_columns,
                        source_provenance, validation_status, is_active)
                       VALUES (2022, 'Pozorrubio', %s, %s, 'validated', true)
                       RETURNING id""",
                    (Jsonb(columns), Jsonb({"files": files})),
                ).fetchone()[0]
                tables = {"codebook": "codebook_values", "mpi_mapping": "mpi_code_mappings",
                          "definitions": "variable_definitions"}
                for name, table in tables.items():
                    statement = sql.SQL(
                        "INSERT INTO {} (batch_id, source_row_number, responses) "
                        "VALUES (%s, %s, %s)"
                    ).format(sql.Identifier(table))
                    with connection.cursor() as cursor:
                        cursor.executemany(statement, (
                            (cls.batch_id, position, Jsonb(row))
                            for position, row in enumerate(
                                reference_frames[name].to_dict("records"), 1)
                        ))
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)),
            )

    def test_references_and_audit_are_batch_scoped(self):
        with psycopg.connect(self.url) as connection:
            references, version = load_import_references(connection, self.batch_id)
            self.assertEqual(set(references), set(REFERENCE_FILES))
            self.assertEqual(len(version), 64)
            import_validation_events.record_attempt(
                connection, self.actor_id, "passed", self.batch_id, version,
                {"household": {"bytes": 42, "sha256": "b" * 64}}, 0, 1,
            )
            event = connection.execute(
                """SELECT outcome, file_metadata, error_count, warning_count
                   FROM import_validation_events WHERE outcome = 'passed'
                   ORDER BY id DESC LIMIT 1""",
            ).fetchone()
            self.assertEqual(event[0], "passed")
            self.assertEqual(event[1]["household"]["bytes"], 42)
            self.assertEqual(event[2:], (0, 1))
            connection.execute(
                """UPDATE import_validation_events
                   SET attempted_at = CURRENT_TIMESTAMP - INTERVAL '91 days'
                   WHERE outcome = 'passed'""",
            )
            self.assertEqual(import_validation_events.purge_expired_attempts(connection), 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM import_validation_events WHERE outcome = 'passed'",
            ).fetchone()[0], 0)

    def test_staging_preserves_active_batch_and_rolls_back_failures(self):
        datasets, references = synthetic_csv_frames()
        with TemporaryDirectory() as directory:
            write_csv_fixture(Path(directory), datasets, references)
            datasets, references, report = load_csv_bundle(directory)
        self.assertTrue(report["passed"], report)
        files = {
            name: {**frame.attrs, "records": len(frame)}
            for name, frame in {**datasets, **references}.items()
        }
        provenance = {
            "declared": {"bundle_sha256": "b" * 64, "source": "synthetic_test"},
            "files": files,
        }
        with psycopg.connect(self.url) as connection:
            with connection.transaction():
                staged = stage_validated_snapshot(
                    connection, datasets, references, provenance,
                )
                import_validation_events.record_attempt(
                    connection, self.actor_id, "staged", self.batch_id,
                    "v1", {}, 0, 0, staged,
                )
            state = connection.execute(
                """SELECT id, validation_status, is_active
                   FROM dataset_batches ORDER BY id""",
            ).fetchall()
            self.assertEqual(state, [
                (self.batch_id, "validated", True),
                (staged, "validated", False),
            ])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM households WHERE batch_id = %s", (staged,),
            ).fetchone()[0], len(datasets["household"]))
            for name, table in (
                    ("codebook", "codebook_values"),
                    ("mpi_mapping", "mpi_code_mappings"),
                    ("definitions", "variable_definitions")):
                self.assertEqual(connection.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE batch_id = %s").format(
                        sql.Identifier(table)), (staged,),
                ).fetchone()[0], len(references[name]))
            with self.assertRaises(DuplicateImportBundle), connection.transaction():
                stage_validated_snapshot(connection, datasets, references, provenance)
            failure_provenance = {
                **provenance,
                "declared": {**provenance["declared"], "bundle_sha256": "c" * 64},
            }
            with patch("services.cbms_import.repository.verify_batch_counts",
                       side_effect=RuntimeError("synthetic mismatch")):
                with self.assertRaises(RuntimeError), connection.transaction():
                    stage_validated_snapshot(
                        connection, datasets, references, failure_provenance,
                    )
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM dataset_batches",
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM import_validation_events WHERE batch_id = %s",
                (staged,),
            ).fetchone()[0], 1)
            connection.execute(
                """UPDATE import_validation_events SET attempted_at =
                   CURRENT_TIMESTAMP - INTERVAL '91 days' WHERE batch_id = %s""",
                (staged,),
            )
            self.assertEqual(import_validation_events.purge_expired_attempts(connection), 0)

    def test_upload_service_stores_only_inactive_synthetic_batch(self):
        datasets, references = synthetic_csv_frames()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv_fixture(root, datasets, references)
            uploads = MultiDict([
                (name, FileStorage(
                    stream=BytesIO((root / "raw" / f"{name}.csv").read_bytes()),
                    filename=f"{name}.csv",
                ))
                for name in datasets
            ])
            app = create_app({
                "TESTING": True, "DATABASE_URL": self.url,
                "IMPORT_STAGING_DIR": str(root / "staging"),
            })
            with app.app_context():
                result = validate_admin_upload(
                    uploads, self.actor_id, store_inactive=True,
                    authorization_confirmed=True,
                )
            self.assertEqual(list((root / "staging").iterdir()), [])
        self.assertTrue(result["report"]["passed"], result)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["batch_id"])
        with psycopg.connect(self.url) as connection:
            state = connection.execute(
                """SELECT validation_status, is_active FROM dataset_batches
                   WHERE id = %s""", (result["batch_id"],),
            ).fetchone()
            self.assertEqual(state, ("validated", False))
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], self.batch_id)
            self.assertEqual(connection.execute(
                """SELECT outcome FROM import_validation_events
                   WHERE batch_id = %s""", (result["batch_id"],),
            ).fetchone()[0], "staged")

    def test_failed_upload_does_not_create_or_activate_a_batch(self):
        datasets, references = synthetic_csv_frames()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv_fixture(root, datasets, references)
            uploads = MultiDict([
                (name, FileStorage(
                    stream=BytesIO(
                        b"uuid,uuid\nPRIVATE,PRIVATE\n" if name == "household"
                        else (root / "raw" / f"{name}.csv").read_bytes()
                    ),
                    filename=f"{name}.csv",
                ))
                for name in datasets
            ])
            app = create_app({
                "TESTING": True, "DATABASE_URL": self.url,
                "IMPORT_STAGING_DIR": str(root / "staging"),
            })
            with psycopg.connect(self.url) as connection:
                before = connection.execute(
                    "SELECT count(*) FROM dataset_batches",
                ).fetchone()[0]
            with app.app_context():
                result = validate_admin_upload(
                    uploads, self.actor_id, store_inactive=True,
                    authorization_confirmed=True,
                )
            self.assertEqual(list((root / "staging").iterdir()), [])
        self.assertFalse(result["report"]["passed"])
        self.assertIsNone(result["batch_id"])
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM dataset_batches",
            ).fetchone()[0], before)
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], self.batch_id)

    def test_z_inactive_batch_preparation_designates_verified_run_and_rolls_back(self):
        datasets, references = synthetic_csv_frames()
        with TemporaryDirectory() as directory:
            write_csv_fixture(Path(directory), datasets, references)
            datasets, references, report = load_csv_bundle(directory)
        self.assertTrue(report["passed"], report)
        files = {
            name: {**frame.attrs, "records": len(frame)}
            for name, frame in {**datasets, **references}.items()
        }

        def stage(bundle_hash):
            provenance = {
                "declared": {
                    "source": "administrator_uploaded_2022_compatible_csv",
                    "bundle_sha256": bundle_hash,
                    "reference_version": "synthetic-reference-v1",
                },
                "files": files,
            }
            with psycopg.connect(self.url) as connection:
                return stage_validated_snapshot(
                    connection, datasets, references, provenance,
                )

        prepared_batch = stage("d" * 64)
        factory = lambda: psycopg.connect(self.url)
        result = prepare_import_batch(
            prepared_batch, self.actor_id, connection_factory=factory,
        )
        self.assertEqual(result["households"], len(datasets["household"]))
        self.assertEqual(result["subsidy_findings"], 4 * result["households"])
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], self.batch_id)
            self.assertEqual(connection.execute(
                "SELECT status FROM assessment_runs WHERE id = %s",
                (result["run_id"],),
            ).fetchone()[0], "completed")
            self.assertEqual(connection.execute(
                "SELECT run_id FROM assessment_run_selections WHERE batch_id = %s",
                (prepared_batch,),
            ).fetchone()[0], result["run_id"])
            self.assertEqual(list_import_batches(factory)[0]["designated_run_id"],
                             result["run_id"])
        with self.assertRaisesRegex(AssessmentPreparationError, "batch_not_preparable"):
            prepare_import_batch(prepared_batch, self.actor_id, connection_factory=factory)

        rollback_batch = stage("e" * 64)
        with patch("services.import_assessment_preparation._verify_stored",
                   side_effect=AssessmentPreparationError("synthetic_verification_failure")):
            with self.assertRaises(AssessmentPreparationError):
                prepare_import_batch(
                    rollback_batch, self.actor_id, connection_factory=factory,
                )
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM assessment_runs WHERE batch_id = %s",
                (rollback_batch,),
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], self.batch_id)
