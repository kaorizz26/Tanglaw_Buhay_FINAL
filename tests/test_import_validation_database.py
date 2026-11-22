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
from flask import g
from werkzeug.datastructures import FileStorage, MultiDict
from werkzeug.security import generate_password_hash

from app import create_app
from repositories import import_validation_events
from cbms_csv_fixtures import synthetic_csv_frames, write_csv_fixture
from services.cbms_import import (
    DuplicateImportBundle, DuplicateSurveyYear, stage_validated_snapshot,
)
from services.cbms_preflight import load_csv_bundle
from services.data_loader import REFERENCE_FILES
from services.import_references import load_import_references
from services.import_validation import validate_admin_upload
from services.import_assessment_preparation import (
    AssessmentPreparationError, list_import_batches, prepare_import_batch,
)
from services.import_batch_deletion import BatchDeletionError, delete_import_batch
from services.batch_activation import (
    active_dataset_exists, activate_prepared_batch, activation_overview,
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
                    "0015_multi_year_import.sql",
                    "0018_import_batch_deletion_audit.sql",
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
                survey_year=2024,
            )
            event = connection.execute(
                """SELECT outcome, survey_year, file_metadata, error_count, warning_count
                   FROM import_validation_events WHERE outcome = 'passed'
                   ORDER BY id DESC LIMIT 1""",
            ).fetchone()
            self.assertEqual(event[0], "passed")
            self.assertEqual(event[1], 2024)
            self.assertEqual(event[2]["household"]["bytes"], 42)
            self.assertEqual(event[3:], (0, 1))
            history = import_validation_events.list_history(connection)
            self.assertEqual(history[0]["outcome"], "passed")
            self.assertEqual(history[0]["uploaded_by"], "synthetic_admin")
            self.assertEqual(history[0]["action_by"], "synthetic_admin")
            self.assertIsNone(history[0]["deleted_at"])
            self.assertEqual(history[0]["survey_year"], 2024)
            self.assertEqual(history[0]["file_metadata"]["household"]["bytes"], 42)
            connection.execute(
                """UPDATE import_validation_events
                   SET attempted_at = CURRENT_TIMESTAMP - INTERVAL '91 days'
                   WHERE outcome = 'passed'""",
            )
            self.assertEqual(import_validation_events.purge_expired_attempts(connection), 1)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM import_validation_events WHERE outcome = 'passed'",
            ).fetchone()[0], 0)

    def test_import_history_filters_counts_and_paginates_in_database(self):
        connection = psycopg.connect(self.url)
        try:
            actor_id = connection.execute(
                """INSERT INTO staff_users (username, password_hash, role)
                   SELECT 'pagination_admin', password_hash, role
                   FROM staff_users WHERE id = %s
                   RETURNING id""",
                (self.actor_id,),
            ).fetchone()[0]
            metadata = {
                name: {"records": index}
                for index, name in enumerate(
                    ("household", "section_a_e_indiv", "section_l02_to_l41",
                     "section_l11_to_l14"),
                    start=1,
                )
            }
            for index in range(30):
                event_metadata = {
                    **metadata,
                    "household": {"records": index + 1},
                }
                import_validation_events.record_attempt(
                    connection,
                    actor_id,
                    "passed" if index % 2 == 0 else "rejected",
                    self.batch_id,
                    "synthetic-reference",
                    event_metadata,
                    0,
                    0,
                    survey_year=2024,
                )

            self.assertEqual(
                import_validation_events.count_history(
                    connection, search="pagination", survey_year=2024,
                ),
                30,
            )
            self.assertEqual(
                import_validation_events.count_history(
                    connection, search="pagination", outcome="passed",
                    survey_year=2024,
                ),
                15,
            )
            first_page = import_validation_events.list_history_page(
                connection, search="pagination", survey_year=2024,
                sort="oldest", limit=25, offset=0,
            )
            second_page = import_validation_events.list_history_page(
                connection, search="pagination", survey_year=2024,
                sort="oldest", limit=25, offset=25,
            )
            self.assertEqual(len(first_page), 25)
            self.assertEqual(len(second_page), 5)
            self.assertLess(first_page[-1]["id"], second_page[0]["id"])
            self.assertTrue(all(
                event["action_by"] == "pagination_admin" for event in second_page
            ))
            records_descending = import_validation_events.list_history_page(
                connection, search="pagination", survey_year=2024,
                sort="records_desc", limit=2, offset=0,
            )
            self.assertGreater(
                records_descending[0]["id"], records_descending[1]["id"],
            )
            self.assertIn(2024, import_validation_events.list_history_years(connection))
        finally:
            connection.rollback()
            connection.close()

    def test_multi_year_migration_keeps_lower_boundary(self):
        with psycopg.connect(self.url) as connection:
            with connection.transaction(force_rollback=True):
                later = connection.execute(
                    """INSERT INTO dataset_batches
                       (survey_year, municipality, source_columns, source_provenance)
                       VALUES (2024, 'Pozorrubio', '{}'::jsonb, '{}'::jsonb)
                       RETURNING survey_year""",
                ).fetchone()[0]
                self.assertEqual(later, 2024)
                with self.assertRaises(psycopg.errors.CheckViolation), connection.transaction():
                    connection.execute(
                        """INSERT INTO dataset_batches
                           (survey_year, municipality, source_columns, source_provenance)
                           VALUES (2021, 'Pozorrubio', '{}'::jsonb, '{}'::jsonb)""",
                    )

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
                    connection, datasets, references, provenance, 2024,
                )
                import_validation_events.record_attempt(
                    connection, self.actor_id, "staged", self.batch_id,
                    "v1", {}, 0, 0, staged,
                    survey_year=2024,
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
            # Source-file equality is year-independent: relabelling the same files
            # as a later survey cannot create another stored dataset.
            with self.assertRaises(DuplicateImportBundle), connection.transaction():
                stage_validated_snapshot(
                    connection, datasets, references, provenance, 2026,
                )
            different_provenance = {
                **provenance,
                "declared": {**provenance["declared"], "bundle_sha256": "d" * 64},
                "files": {
                    **files,
                    "household": {**files["household"], "sha256": "d" * 64},
                },
            }
            with self.assertRaises(DuplicateSurveyYear), connection.transaction():
                stage_validated_snapshot(
                    connection, datasets, references, different_provenance, 2024,
                )
            failure_provenance = {
                **provenance,
                "declared": {**provenance["declared"], "bundle_sha256": "c" * 64},
                "files": {
                    **files,
                    "household": {**files["household"], "sha256": "c" * 64},
                },
            }
            with patch("services.cbms_import.repository.verify_batch_counts",
                       side_effect=RuntimeError("synthetic mismatch")):
                with self.assertRaises(RuntimeError), connection.transaction():
                    stage_validated_snapshot(
                        connection, datasets, references, failure_provenance,
                        2024, same_year_confirmed=True,
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
        datasets["household"].insert(
            3, "contact_number", ["09990000001", "+639990000002"],
        )
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
                "IMPORT_REFERENCE_DIR": None,
            })
            with app.app_context():
                result = validate_admin_upload(
                    uploads, self.actor_id, 2024, store_inactive=True,
                    authorization_confirmed=True, same_year_confirmed=True,
                )
            self.assertEqual(list((root / "staging").iterdir()), [])
        self.assertTrue(result["report"]["passed"], result)
        self.assertIsNone(result["error"])
        self.assertIsNotNone(result["batch_id"])
        with psycopg.connect(self.url) as connection:
            state = connection.execute(
                """SELECT survey_year, validation_status, is_active FROM dataset_batches
                   WHERE id = %s""", (result["batch_id"],),
            ).fetchone()
            self.assertEqual(state, (2024, "validated", False))
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], self.batch_id)
            self.assertEqual(connection.execute(
                """SELECT outcome, survey_year FROM import_validation_events
                   WHERE batch_id = %s""", (result["batch_id"],),
            ).fetchone(), ("staged", 2024))

    def test_delete_inactive_batch_removes_data_and_retains_audit(self):
        datasets, references = synthetic_csv_frames()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv_fixture(root, datasets, references)
            datasets, references, report = load_csv_bundle(directory)
        self.assertTrue(report["passed"], report)
        provenance = {
            "declared": {
                "source": "administrator_uploaded_compatible_csv",
                "survey_year": 2024,
                "bundle_sha256": "f" * 64,
                "reference_version": "synthetic-reference-v1",
            },
            "files": {
                name: {**frame.attrs, "records": len(frame)}
                for name, frame in {**datasets, **references}.items()
            },
        }
        with psycopg.connect(self.url) as connection:
            batch_id = stage_validated_snapshot(
                connection, datasets, references, provenance, 2024,
            )
            import_validation_events.record_attempt(
                connection, self.actor_id, "staged", self.batch_id,
                "synthetic-reference-v1", provenance["files"], 0, 0,
                batch_id, survey_year=2024,
            )

        factory = lambda: psycopg.connect(self.url)
        prepared = prepare_import_batch(
            batch_id, self.actor_id, connection_factory=factory,
        )
        result = delete_import_batch(
            batch_id, self.actor_id, f"DELETE BATCH {batch_id}",
            connection_factory=factory,
        )
        self.assertEqual(result["households"], len(datasets["household"]))
        self.assertEqual(result["assessment_runs"], 1)
        self.assertEqual(prepared["households"], result["households"])

        with psycopg.connect(self.url) as connection:
            self.assertIsNone(connection.execute(
                "SELECT id FROM dataset_batches WHERE id = %s", (batch_id,),
            ).fetchone())
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM households WHERE batch_id = %s", (batch_id,),
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM assessment_runs WHERE batch_id = %s", (batch_id,),
            ).fetchone()[0], 0)
            audit = connection.execute(
                """SELECT outcome, batch_id, deleted_batch_id,
                          deleted_at IS NOT NULL, deleted_by
                   FROM import_validation_events WHERE deleted_batch_id = %s""",
                (batch_id,),
            ).fetchone()
            self.assertEqual(audit, ("deleted", None, batch_id, True, self.actor_id))
            history = import_validation_events.list_history(connection)
            deleted = next(row for row in history if row["outcome"] == "deleted")
            self.assertEqual(deleted["uploaded_by"], "synthetic_admin")
            self.assertEqual(deleted["action_by"], "synthetic_admin")
            self.assertIsNotNone(deleted["deleted_at"])
            connection.execute(
                """UPDATE import_validation_events SET attempted_at =
                   CURRENT_TIMESTAMP - INTERVAL '91 days'
                   WHERE deleted_batch_id = %s""",
                (batch_id,),
            )
            self.assertEqual(import_validation_events.purge_expired_attempts(connection), 0)

    def test_active_batch_cannot_be_deleted(self):
        factory = lambda: psycopg.connect(self.url)
        with self.assertRaisesRegex(BatchDeletionError, "active_batch"):
            delete_import_batch(
                self.batch_id, self.actor_id,
                f"DELETE BATCH {self.batch_id}", connection_factory=factory,
            )

    def test_upload_keeps_contact_numbers_as_original_text(self):
        datasets, references = synthetic_csv_frames()
        sent = ["09171234567", "+639171234567"]
        datasets["household"].insert(3, "contact_number", sent)
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
                "IMPORT_REFERENCE_DIR": None,
            })
            with app.app_context():
                result = validate_admin_upload(
                    uploads, self.actor_id, 2024, store_inactive=True,
                    authorization_confirmed=True, same_year_confirmed=True,
                )
        self.assertTrue(result["report"]["passed"], result)
        with psycopg.connect(self.url) as connection:
            stored = connection.execute(
                """SELECT responses ->> 'contact_number',
                          jsonb_typeof(responses -> 'contact_number')
                   FROM households WHERE batch_id = %s ORDER BY source_row_number""",
                (result["batch_id"],),
            ).fetchall()
            columns = connection.execute(
                "SELECT source_columns -> 'household' FROM dataset_batches WHERE id = %s",
                (result["batch_id"],),
            ).fetchone()[0]
        # Leading zero and plus sign survive, as JSON text rather than numbers.
        self.assertEqual(stored, [(sent[0], "string"), (sent[1], "string")])
        self.assertEqual(columns.index("contact_number"), 3)

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
                "IMPORT_REFERENCE_DIR": None,
            })
            with psycopg.connect(self.url) as connection:
                before = connection.execute(
                    "SELECT count(*) FROM dataset_batches",
                ).fetchone()[0]
            with app.app_context():
                result = validate_admin_upload(
                    uploads, self.actor_id, 2024, store_inactive=True,
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
            distinct_files = {
                **files,
                "household": {**files["household"], "sha256": bundle_hash},
            }
            provenance = {
                "declared": {
                    "source": "administrator_uploaded_compatible_csv",
                    "survey_year": 2024,
                    "bundle_sha256": bundle_hash,
                    "reference_version": "synthetic-reference-v1",
                },
                "files": distinct_files,
            }
            with psycopg.connect(self.url) as connection:
                return stage_validated_snapshot(
                    connection, datasets, references, provenance, 2024,
                    same_year_confirmed=True,
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


@unittest.skipUnless(TEST_URL, "Set TANGLAW_TEST_DATABASE_URL for isolated database checks.")
class FirstDatasetDatabaseTests(unittest.TestCase):
    """Exercise the administrator workflow from an empty dataset database."""

    @classmethod
    def setUpClass(cls):
        settings = conninfo_to_dict(TEST_URL)
        if (settings.get("dbname") != "tanglaw_buhay_test"
                or settings.get("host") not in ("localhost", "127.0.0.1", "::1")
                or settings.get("hostaddr", "127.0.0.1") not in ("127.0.0.1", "::1")
                or "service" in settings):
            raise ValueError("Synthetic checks require the local test database.")
        cls.schema = f"first_import_{uuid4().hex}"
        cls.url = make_conninfo(
            TEST_URL, hostaddr="127.0.0.1", connect_timeout=5,
            options=f"-c search_path={cls.schema}",
        )
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)),
            )
        with psycopg.connect(cls.url) as connection:
            for name in (
                    "0001_database_foundation.sql",
                    "0002_staff_authentication.sql",
                    "0003_assessment_persistence.sql",
                    "0004_assessment_run_selection.sql",
                    "0013_import_validation_audit.sql",
                    "0014_batch_activation_audit.sql",
                    "0015_multi_year_import.sql",
                    "0018_import_batch_deletion_audit.sql",
                    "0019_initial_dataset_activation.sql",
            ):
                connection.execute((ROOT / "migrations" / name).read_text())
            cls.actor_id = connection.execute(
                """INSERT INTO staff_users (username, password_hash, role)
                   VALUES ('first_admin', %s, 'system_administrator') RETURNING id""",
                (generate_password_hash("synthetic-first-password", method="scrypt"),),
            ).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(TEST_URL, hostaddr="127.0.0.1", autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)),
            )

    def test_admin_imports_prepares_and_activates_first_dataset(self):
        datasets, references = synthetic_csv_frames()
        factory = lambda: psycopg.connect(self.url)
        self.assertFalse(active_dataset_exists(connection_factory=factory))
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
                "IMPORT_REFERENCE_DIR": str(root / "reference"),
                "IMPORT_REFERENCE_BATCH_ID": None,
            })
            with app.app_context():
                imported = validate_admin_upload(
                    uploads, self.actor_id, 2022, store_inactive=True,
                    authorization_confirmed=True,
                )
        self.assertTrue(imported["report"]["passed"], imported)
        batch_id = imported["batch_id"]
        prepared = prepare_import_batch(
            batch_id, self.actor_id, connection_factory=factory,
        )
        overview = activation_overview(connection_factory=factory)
        self.assertTrue(overview["initial_setup"])
        self.assertIsNone(overview["active"])
        self.assertEqual(overview["candidates"][0]["id"], batch_id)
        activated = activate_prepared_batch(
            batch_id, self.actor_id,
            expected_active_batch_id=None, expected_active_run_id=None,
            expected_target_run_id=prepared["run_id"],
            connection_factory=factory,
        )
        self.assertEqual(activated["action"], "initial_activate")
        self.assertTrue(active_dataset_exists(connection_factory=factory))
        with psycopg.connect(self.url) as connection:
            self.assertEqual(connection.execute(
                "SELECT id FROM dataset_batches WHERE is_active",
            ).fetchone()[0], batch_id)
            self.assertEqual(connection.execute(
                """SELECT reference_batch_id, reference_version IS NOT NULL
                   FROM import_validation_events WHERE batch_id = %s""",
                (batch_id,),
            ).fetchone(), (None, True))
            self.assertEqual(connection.execute(
                """SELECT action, previous_batch_id, previous_run_id,
                          target_batch_id, target_run_id
                   FROM batch_activation_events""",
            ).fetchone(), (
                "initial_activate", None, None, batch_id, prepared["run_id"],
            ))

    def test_route_returns_json_for_exact_names_with_a_missing_column(self):
        datasets, references = synthetic_csv_frames()
        datasets["section_l02_to_l41"] = datasets["section_l02_to_l41"].drop(
            columns=["barangay"],
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv_fixture(root, datasets, references)
            app = create_app({
                "TESTING": True,
                "DATABASE_URL": self.url,
                "SECRET_KEY": "synthetic-route-secret",
                "IMPORT_STAGING_DIR": str(root / "staging"),
                "IMPORT_REFERENCE_DIR": str(ROOT / "data" / "reference"),
                "IMPORT_REFERENCE_BATCH_ID": None,
            })
            client = app.test_client()

            def load_user():
                g.user = {
                    "id": self.actor_id,
                    "username": "first_admin",
                    "role": "system_administrator",
                }

            with patch("services.auth.authentication_configured", return_value=True), \
                 patch("services.auth.load_session_user", side_effect=load_user):
                with client.session_transaction() as session:
                    session["csrf_token"] = "synthetic-csrf"
                response = client.post(
                    "/administration/import",
                    data={
                        "csrf_token": "synthetic-csrf",
                        "survey_year": "2022",
                        **{
                            name: (
                                BytesIO((root / "raw" / f"{name}.csv").read_bytes()),
                                f"{name}.csv",
                            )
                            for name in datasets
                        },
                    },
                    headers={
                        "X-Requested-With": "fetch",
                        "Accept": "application/json",
                    },
                )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "application/json")
        preview = response.get_json()["preview_html"]
        self.assertIn("blocking", preview)
        self.assertIn("Missing required column", preview)
        self.assertIn("<code>barangay</code>", preview)
