"""Synthetic checks for the admin-only four-file validation stage."""

from contextlib import nullcontext
from io import BytesIO
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from flask import g
from werkzeug.datastructures import FileStorage, MultiDict

from app import create_app
from cbms_csv_fixtures import synthetic_csv_frames, write_csv_fixture
from services.cbms_preflight import load_csv_bundle, validate_csv_bundle
from services.data_loader import EXCEL_FILES, REFERENCE_FILES
from services.import_references import load_import_references
from services.import_validation import (
    ImportValidationUnavailable, STAGING_MARKER, _cleanup_stale,
    validate_admin_upload,
)
from services.batch_activation import BatchActivationError
from services.survey_years import (
    is_supported_survey_year, latest_survey_year, parse_survey_year,
    supported_survey_years,
)


class SurveyYearRulesTest(unittest.TestCase):
    def test_supported_years_follow_two_year_cycle_from_2022(self):
        years = supported_survey_years()

        self.assertEqual(years[0], 2022)
        self.assertEqual(years[-1], latest_survey_year())
        self.assertTrue(all(year % 2 == 0 for year in years))
        self.assertTrue(all(right - left == 2
                            for left, right in zip(years, years[1:])))

    def test_odd_year_is_rejected_by_shared_validation(self):
        self.assertFalse(is_supported_survey_year(2023))
        with self.assertRaisesRegex(ValueError, "invalid_survey_year"):
            parse_survey_year("2023")


class FakeConnection:
    def __init__(self, locked=True):
        self.locked = locked

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, _=None):
        self.statement = statement
        return self

    def fetchone(self):
        return (self.locked,)

    def commit(self):
        pass

    def transaction(self):
        return nullcontext()


class ImportValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.datasets, self.references = synthetic_csv_frames()
        write_csv_fixture(self.root, self.datasets, self.references)
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48),
            "IMPORT_STAGING_DIR": str(self.root / "staging"),
        })

    def _files(self):
        return MultiDict([
            (name, FileStorage(
                stream=BytesIO((self.root / "raw" / f"{name}.csv").read_bytes()),
                filename=f"{name}.csv",
            ))
            for name in EXCEL_FILES
        ])

    def test_preflight_uses_supplied_system_references(self):
        for name in REFERENCE_FILES.values():
            (self.root / "reference" / name).unlink()
        report = validate_csv_bundle(self.root, reference_frames=self.references)
        self.assertTrue(report["passed"], report)
        self.assertEqual(set(report["references"]), set(REFERENCE_FILES))

    def test_validation_cleans_uploads_and_records_metadata_only(self):
        with self.app.app_context():
            with patch("services.import_validation.load_import_references",
                       return_value=(self.references, "version")):
                with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                    with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                        result = validate_admin_upload(
                            self._files(), 7, 2024,
                            connection_factory=FakeConnection,
                        )
        self.assertTrue(result["report"]["passed"], result)
        self.assertIsNone(result["error"])
        self.assertEqual(list((self.root / "staging").iterdir()), [])
        self.assertEqual(audit.call_args.args[1:4], (7, "passed", 1))
        metadata = audit.call_args.args[5]
        self.assertEqual(set(metadata), set(EXCEL_FILES))
        self.assertEqual(set(metadata["household"]), {"bytes", "sha256"})
        self.assertNotIn("DEV-0001", str(audit.call_args))

    def test_passing_bundle_can_be_stored_inactive(self):
        _, stored_references, _ = load_csv_bundle(self.root)
        with self.app.app_context():
            with patch("services.import_validation.load_import_references",
                       return_value=(stored_references, "v1")):
                with patch("services.import_validation.stage_validated_snapshot",
                           return_value=8) as stage:
                    with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                        with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                            result = validate_admin_upload(
                                self._files(), 7, 2024, store_inactive=True,
                                authorization_confirmed=True,
                                connection_factory=FakeConnection,
                            )
        self.assertEqual(result["batch_id"], 8)
        self.assertTrue(result["report"]["passed"])
        stage.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "staged")
        self.assertEqual(audit.call_args.args[-1], 8)
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_storage_failure_is_generic_and_audited(self):
        _, stored_references, _ = load_csv_bundle(self.root)
        with self.app.app_context():
            with patch("services.import_validation.load_import_references",
                       return_value=(stored_references, "v1")):
                with patch("services.import_validation.stage_validated_snapshot",
                           side_effect=RuntimeError("private database detail")):
                    with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                        with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                            with self.assertRaises(ImportValidationUnavailable) as error:
                                validate_admin_upload(
                                    self._files(), 7, 2024, store_inactive=True,
                                    authorization_confirmed=True,
                                    connection_factory=FakeConnection,
                                )
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(audit.call_args.args[2], "rejected")

    def test_bad_filename_is_rejected_without_processing_sources(self):
        files = self._files()
        files["household"].filename = "../household.csv"
        with self.app.app_context():
            with patch("services.import_validation.load_import_references") as references:
                with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                    with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                        result = validate_admin_upload(
                            files, 7, 2024, connection_factory=FakeConnection,
                        )
        self.assertIsNone(result["report"])
        self.assertEqual(audit.call_args.args[2], "rejected")
        references.assert_not_called()

    def test_odd_survey_year_is_rejected_before_processing_sources(self):
        with self.app.app_context():
            with patch("services.import_validation.load_import_references") as references:
                with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                    with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                        result = validate_admin_upload(
                            self._files(), 7, 2023,
                            connection_factory=FakeConnection,
                        )
        self.assertIsNone(result["report"])
        self.assertIn("every two years from 2022", result["error"])
        self.assertIsNone(result["survey_year"])
        self.assertEqual(audit.call_args.args[2], "rejected")
        references.assert_not_called()

    def test_concurrent_validation_is_rejected_and_audited(self):
        with self.app.app_context():
            with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                    result = validate_admin_upload(
                        self._files(), 7, 2024,
                        connection_factory=lambda: FakeConnection(locked=False),
                    )
        self.assertIsNone(result["report"])
        self.assertIn("Another data validation", result["error"])
        self.assertEqual(audit.call_args.args[2], "rejected")

    def test_stale_cleanup_only_removes_marked_upload_directory(self):
        staging = self.root / "staging"
        staging.mkdir()
        abandoned = staging / "tanglaw-import-abandoned"
        unrelated = staging / "tanglaw-import-unrelated"
        for directory in (abandoned, unrelated):
            directory.mkdir()
        (abandoned / STAGING_MARKER).write_text("validation", encoding="ascii")
        for directory in (abandoned, unrelated):
            os.utime(directory, (1, 1))
        _cleanup_stale(staging.resolve())
        self.assertFalse(abandoned.exists())
        self.assertTrue(unrelated.exists())

    def test_reference_snapshot_checks_counts_and_provenance(self):
        columns = {name: list(frame.columns) for name, frame in self.references.items()}
        files = {
            name: {"filename": filename, "records": len(self.references[name]),
                   "sha256": "a" * 64}
            for name, filename in REFERENCE_FILES.items()
        }
        batch = {"id": 1, "survey_year": 2022, "municipality": "Pozorrubio",
                 "source_columns": columns, "source_provenance": {"files": files}}

        def rows(_, __, name):
            for position, record in enumerate(
                    self.references[name].to_dict("records"), 1):
                yield position, record

        with patch("services.import_references.dataset_batches.find_validated_batch_metadata",
                   return_value=batch):
            with patch("services.import_references.dataset_batches.iter_batch_records",
                       side_effect=rows):
                loaded, version = load_import_references(None, 1)
        self.assertEqual(set(loaded), set(REFERENCE_FILES))
        self.assertEqual(len(version), 64)
        for name in REFERENCE_FILES:
            self.assertTrue(loaded[name].equals(self.references[name]))


class ImportRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True, "SECRET_KEY": token_urlsafe(48)})
        self.client = self.app.test_client()
        self.auth_config = patch("services.auth.authentication_configured", return_value=True)
        self.auth_config.start()
        self.addCleanup(self.auth_config.stop)
        self.role = "system_administrator"

        def load_user():
            g.user = {"id": 7, "username": "synthetic_admin", "role": self.role}

        self.auth_user = patch("services.auth.load_session_user", side_effect=load_user)
        self.auth_user.start()
        self.addCleanup(self.auth_user.stop)

    def test_admin_can_open_and_staff_cannot_open_import_page(self):
        response = self.client.get("/administration/import")
        self.assertEqual(response.status_code, 200)
        for name in EXCEL_FILES:
            self.assertIn(f"{name}.csv".encode(), response.data)
        for filename in REFERENCE_FILES.values():
            self.assertNotIn(filename.encode(), response.data)
        self.assertIn(b'name="survey_year"', response.data)
        self.assertIn(b'<select id="survey-year"', response.data)
        self.assertIn(b'<option value="2022">2022</option>', response.data)
        self.assertIn(b'<option value="2024">2024</option>', response.data)
        self.assertNotIn(b'<option value="2023">', response.data)
        self.role = "lgu_staff"
        self.assertEqual(self.client.get("/administration/import").status_code, 403)

    def test_post_requires_supported_year_and_never_imports_data(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        self.assertEqual(self.client.post("/administration/import").status_code, 400)
        report = {"passed": True, "datasets": {}, "errors": [], "warnings": []}
        with patch("routes.main.validate_admin_upload") as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2035",
            })
            self.assertEqual(response.status_code, 400)
            validate.assert_not_called()
        with patch("routes.main.validate_admin_upload") as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2023",
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"every two years from 2022", response.data)
            validate.assert_not_called()
        with patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None}) as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2024",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No dataset has been added or activated", response.data)
        validate.assert_called_once()

    def test_store_action_requires_confirmation_and_reports_inactive_batch(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {"passed": True, "datasets": {}, "errors": [], "warnings": []}
        with patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None, "batch_id": 8}) as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "survey_year": "2024",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stored survey year 2024 as inactive batch 8", response.data)
        self.assertEqual(validate.call_args.args[2], 2024)
        self.assertTrue(validate.call_args.kwargs["store_inactive"])
        self.assertTrue(validate.call_args.kwargs["authorization_confirmed"])

    def test_oversized_request_gets_fixed_response_and_audit(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        self.app.config["IMPORT_MAX_CONTENT_LENGTH"] = 100
        with patch("routes.main.record_oversized_attempt") as audit:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "padding": "x" * 1024,
            })
        self.assertEqual(response.status_code, 413)
        self.assertIn(b"256 MiB upload limit", response.data)
        audit.assert_called_once_with(7)

    def test_admin_preparation_page_and_action_require_confirmation(self):
        batches = [{"id": 8, "survey_year": 2022, "is_active": False,
                    "households": 2, "run_count": 0, "designated_run_id": None}]
        with patch("routes.main.list_import_batches", return_value=batches):
            response = self.client.get("/administration/import/batches")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Run and verify assessments", response.data)
            self.role = "lgu_staff"
            self.assertEqual(self.client.get(
                "/administration/import/batches").status_code, 403)
            self.role = "system_administrator"
            with self.client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            with patch("routes.main.prepare_import_batch") as prepare:
                response = self.client.post("/administration/import/batches", data={
                    "csrf_token": "synthetic-csrf", "batch_id": "8",
                })
                self.assertEqual(response.status_code, 400)
                prepare.assert_not_called()
                response = self.client.post("/administration/import/batches", data={
                    "csrf_token": "synthetic-csrf", "batch_id": "8",
                    "confirm_prepare": "yes",
                })
                self.assertEqual(response.status_code, 200)
                prepare.assert_called_once_with(8, 7)

    def test_activation_page_requires_admin_confirmation_and_fresh_context(self):
        overview = {
            "active": {"id": 1, "survey_year": 2022,
                       "households": 2, "run_id": 3},
            "candidates": [{"id": 8, "survey_year": 2022,
                            "households": 2, "run_id": 4}],
            "in_progress_reviews": 0,
        }
        with patch("routes.main.activation_overview", return_value=overview):
            response = self.client.get("/administration/import/activation")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Switch to Batch 8", response.data)
            self.role = "lgu_staff"
            self.assertEqual(self.client.get(
                "/administration/import/activation").status_code, 403)
            self.role = "system_administrator"
            with self.client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            fields = {
                "csrf_token": "synthetic-csrf",
                "target_batch_id": "8", "expected_active_batch_id": "1",
                "expected_active_run_id": "3", "expected_target_run_id": "4",
            }
            with patch("routes.main.activate_prepared_batch") as switch:
                self.assertEqual(self.client.post(
                    "/administration/import/activation", data=fields,
                ).status_code, 400)
                switch.assert_not_called()
                switch.return_value = {
                    "target_batch_id": 8, "previous_batch_id": 1,
                }
                response = self.client.post(
                    "/administration/import/activation",
                    data={**fields, "confirm_switch": "yes"},
                )
                self.assertEqual(response.status_code, 302)
                switch.assert_called_once_with(
                    8, 7, expected_active_batch_id=1,
                    expected_active_run_id=3, expected_target_run_id=4,
                )

    def test_activation_page_shows_unfinished_review_guard(self):
        overview = {
            "active": {"id": 1, "survey_year": 2022,
                       "households": 2, "run_id": 3},
            "candidates": [{"id": 8, "survey_year": 2022,
                            "households": 2, "run_id": 4}],
            "in_progress_reviews": 1,
        }
        with patch("routes.main.activation_overview", return_value=overview):
            response = self.client.get("/administration/import/activation")
            self.assertEqual(response.status_code, 200)
            # "In Review" is emphasised with <strong>, so compare the visible text.
            visible_text = re.sub(rb"<[^>]+>", b"", response.data)
            self.assertIn(b"1 staff review is still In Review", visible_text)
            self.assertNotIn(b"Switch to Batch 8", response.data)
            with self.client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            with patch("routes.main.activate_prepared_batch",
                       side_effect=BatchActivationError(
                           "unfinished_reviews", review_count=1)):
                response = self.client.post(
                    "/administration/import/activation", data={
                        "csrf_token": "synthetic-csrf", "target_batch_id": "8",
                        "expected_active_batch_id": "1",
                        "expected_active_run_id": "3", "expected_target_run_id": "4",
                        "confirm_switch": "yes",
                    },
                )
                self.assertEqual(response.status_code, 409)
                self.assertIn(b"Finish or cancel them", response.data)
