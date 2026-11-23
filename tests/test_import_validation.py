"""Synthetic checks for the admin-only four-file validation stage."""

from contextlib import nullcontext
import csv
from datetime import datetime, timezone
from io import BytesIO
from io import StringIO
from html import unescape
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from flask import g
from werkzeug.datastructures import FileStorage, MultiDict

from app import create_app
from cbms_csv_fixtures import FIRST_HOUSEHOLD_ID, synthetic_csv_frames, write_csv_fixture
from services.cbms_preflight import load_csv_bundle, validate_csv_bundle
from services.data_loader import EXCEL_FILES, REFERENCE_FILES
from services.import_references import (
    ImportReferenceError, load_import_references, load_system_import_references,
)
from services.import_history import (
    export_import_history, get_import_history, parse_import_history_filters,
)
from services.import_validation import (
    ImportPreviewError, ImportValidationUnavailable, STAGING_MARKER,
    _cleanup_stale, create_import_preview_token, validate_admin_upload,
    verify_import_preview_token,
)
from services.cbms_import import DuplicateImportBundle, DuplicateSurveyYear
from services.batch_activation import BatchActivationError, describe_citizen_work
from services.import_batch_deletion import BatchDeletionError
from services.survey_years import (
    is_supported_survey_year, parse_survey_year, supported_survey_years,
)


class SurveyYearRulesTest(unittest.TestCase):
    def test_established_survey_year_suggestions_are_retained(self):
        self.assertEqual(supported_survey_years(), (2022, 2024, 2026))

    def test_later_four_digit_year_is_accepted_when_it_is_not_in_the_future(self):
        with patch("services.survey_years.latest_survey_year", return_value=2029):
            self.assertTrue(is_supported_survey_year(2023))
            self.assertTrue(is_supported_survey_year(2029))
            self.assertEqual(parse_survey_year("2029"), 2029)
            self.assertFalse(is_supported_survey_year(2030))
            with self.assertRaisesRegex(ValueError, "invalid_survey_year"):
                parse_survey_year("2030")

    def test_year_before_the_first_supported_survey_is_rejected(self):
        self.assertFalse(is_supported_survey_year(2021))
        with self.assertRaisesRegex(ValueError, "invalid_survey_year"):
            parse_survey_year("2021")


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
            "IMPORT_REFERENCE_DIR": None,
        })

    def _files(self):
        return MultiDict([
            (name, FileStorage(
                stream=BytesIO((self.root / "raw" / f"{name}.csv").read_bytes()),
                filename=f"{name}.csv",
            ))
            for name in EXCEL_FILES
        ])

    def test_preview_token_is_limited_to_the_admin_and_survey_year(self):
        with self.app.app_context():
            token = create_import_preview_token(7, 2024, "a" * 64)
            self.assertEqual(verify_import_preview_token(token, 7, 2024), "a" * 64)
            with self.assertRaisesRegex(ImportPreviewError, "does not match"):
                verify_import_preview_token(token, 8, 2024)
            with self.assertRaisesRegex(ImportPreviewError, "does not match"):
                verify_import_preview_token(token, 7, 2022)
            with self.assertRaisesRegex(ImportPreviewError, "invalid"):
                verify_import_preview_token(token + "changed", 7, 2024)

    def test_preflight_uses_supplied_system_references(self):
        for name in REFERENCE_FILES.values():
            (self.root / "reference" / name).unlink()
        report = validate_csv_bundle(self.root, reference_frames=self.references)
        self.assertTrue(report["passed"], report)
        self.assertEqual(set(report["references"]), set(REFERENCE_FILES))

    def test_system_reference_package_is_versioned_and_fails_closed(self):
        loaded, version = load_system_import_references(self.root / "reference")
        self.assertEqual(set(loaded), set(REFERENCE_FILES))
        self.assertRegex(version, r"^[0-9a-f]{64}$")
        (self.root / "reference" / next(iter(REFERENCE_FILES.values()))).unlink()
        with self.assertRaisesRegex(ImportReferenceError, "package_invalid"):
            load_system_import_references(self.root / "reference")

    def test_contact_number_column_is_accepted_and_kept_as_text(self):
        datasets = {name: frame.copy() for name, frame in self.datasets.items()}
        datasets["household"].insert(3, "contact_number", ["09171234567", "+639171234567"])
        root = self.root / "with_contact_number"
        root.mkdir()
        write_csv_fixture(root, datasets, self.references)
        frames, _, report = load_csv_bundle(root, reference_frames=self.references)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["errors"], [])
        # The column has no system definition, so it only produces a warning.
        self.assertIn({"source": "household", "code": "columns_without_definitions",
                       "count": 1}, report["warnings"])
        contact = frames["household"]["contact_number"]
        self.assertEqual(list(frames["household"].columns).index("contact_number"), 3)
        self.assertEqual(contact.tolist(), ["09171234567", "+639171234567"])
        self.assertTrue(all(isinstance(value, str) for value in contact))

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
        self.assertEqual(set(metadata), {*EXCEL_FILES, "_result"})
        self.assertEqual(
            set(metadata["household"]), {"bytes", "sha256", "records"},
        )
        self.assertEqual(metadata["_result"]["code"], "validation_passed")
        self.assertIn("Validated", metadata["_result"]["description"])
        self.assertFalse(metadata["_result"]["same_year_reupload"])
        self.assertNotIn(FIRST_HOUSEHOLD_ID, str(audit.call_args))

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
                                same_year_confirmed=True,
                                connection_factory=FakeConnection,
                            )
        self.assertEqual(result["batch_id"], 8)
        self.assertTrue(result["report"]["passed"])
        stage.assert_called_once()
        self.assertEqual(audit.call_args.args[2], "staged")
        details = audit.call_args.args[5]["_result"]
        self.assertEqual(details["code"], "staged")
        self.assertTrue(details["same_year_reupload"])
        self.assertIn("separate inactive batch", details["description"])
        self.assertIn("no existing batch was overwritten", details["description"])
        self.assertEqual(audit.call_args.args[-1], 8)
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_storage_rejects_files_that_changed_after_preview(self):
        with self.app.app_context(), \
             patch("services.import_validation.import_validation_events.record_attempt") as audit, \
             patch("services.import_validation.import_validation_events.purge_expired_attempts"):
            result = validate_admin_upload(
                self._files(), 7, 2024, store_inactive=True,
                authorization_confirmed=True,
                expected_preview_digest="0" * 64,
                connection_factory=FakeConnection,
            )
        self.assertIsNone(result["report"])
        self.assertIsNone(result["batch_id"])
        self.assertIn("selected files changed after preview", result["error"])
        self.assertEqual(audit.call_args.args[2], "rejected")
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_exact_source_files_are_blocked_even_with_year_confirmation(self):
        _, stored_references, _ = load_csv_bundle(self.root)
        with self.app.app_context(), \
             patch("services.import_validation.load_import_references",
                   return_value=(stored_references, "v1")), \
             patch("services.import_validation.stage_validated_snapshot",
                   side_effect=DuplicateImportBundle("duplicate_import_bundle")), \
             patch("services.import_validation.import_validation_events.record_attempt") as audit, \
             patch("services.import_validation.import_validation_events.purge_expired_attempts"):
            result = validate_admin_upload(
                self._files(), 7, 2024, store_inactive=True,
                authorization_confirmed=True, same_year_confirmed=True,
                connection_factory=FakeConnection,
            )
        self.assertIsNone(result["batch_id"])
        self.assertIn("exact CBMS source files", result["error"])
        self.assertEqual(audit.call_args.args[2], "rejected")
        details = audit.call_args.args[5]["_result"]
        self.assertEqual(details["code"], "duplicate_bundle")
        self.assertFalse(details["same_year_reupload"])

    def test_same_year_requires_confirmation_before_storage(self):
        _, stored_references, _ = load_csv_bundle(self.root)
        with self.app.app_context(), \
             patch("services.import_validation.load_import_references",
                   return_value=(stored_references, "v1")), \
             patch("services.import_validation.stage_validated_snapshot",
                   side_effect=DuplicateSurveyYear("duplicate_survey_year")), \
             patch("services.import_validation.import_validation_events.record_attempt") as audit, \
             patch("services.import_validation.import_validation_events.purge_expired_attempts"):
            result = validate_admin_upload(
                self._files(), 7, 2024, store_inactive=True,
                authorization_confirmed=True,
                connection_factory=FakeConnection,
            )
        self.assertIsNone(result["batch_id"])
        self.assertIn("Survey year 2024 already has a stored dataset", result["error"])
        self.assertEqual(audit.call_args.args[2], "rejected")

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
        details = audit.call_args.args[5]["_result"]
        self.assertEqual(details["code"], "storage_unavailable")
        self.assertIn("no dataset was changed", details["description"])

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
        self.assertEqual(
            result["error"],
            "Use the exact filename household.csv for Households.",
        )
        self.assertEqual(audit.call_args.args[2], "rejected")
        references.assert_not_called()

    def test_non_csv_file_type_is_rejected_clearly(self):
        files = self._files()
        files["household"].filename = "household.txt"
        with self.app.app_context(), \
             patch("services.import_validation.load_import_references") as references, \
             patch("services.import_validation.import_validation_events.record_attempt"), \
             patch("services.import_validation.import_validation_events.purge_expired_attempts"):
            result = validate_admin_upload(
                files, 7, 2024, connection_factory=FakeConnection,
            )
        self.assertEqual(
            result["error"],
            "Households must be a CSV file named household.csv.",
        )
        references.assert_not_called()

    def test_oversized_individual_file_is_rejected_with_its_required_name(self):
        files = self._files()
        files["household"].stream = BytesIO(b"x" * 11)
        with self.app.app_context(), \
             patch("services.import_validation.FILE_UPLOAD_BYTES", 10), \
             patch("services.import_validation.load_import_references") as references, \
             patch("services.import_validation.import_validation_events.record_attempt") as audit, \
             patch("services.import_validation.import_validation_events.purge_expired_attempts"):
            result = validate_admin_upload(
                files, 7, 2024, connection_factory=FakeConnection,
            )
        self.assertIsNone(result["report"])
        self.assertEqual(
            result["error"],
            "household.csv exceeds the 64 MB file limit. Choose a smaller file.",
        )
        self.assertEqual(audit.call_args.args[2], "rejected")
        references.assert_not_called()
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_out_of_range_survey_year_is_rejected_before_processing_sources(self):
        with self.app.app_context():
            with patch("services.import_validation.load_import_references") as references:
                with patch("services.import_validation.import_validation_events.record_attempt") as audit:
                    with patch("services.import_validation.import_validation_events.purge_expired_attempts"):
                        result = validate_admin_upload(
                            self._files(), 7, 2021,
                            connection_factory=FakeConnection,
                        )
        self.assertIsNone(result["report"])
        self.assertIn("four-digit survey year from 2022", result["error"])
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


class ImportHistoryTests(unittest.TestCase):
    def test_page_history_is_aggregate_and_limited(self):
        metadata = {name: {"records": 2} for name in EXCEL_FILES}
        metadata["_result"] = {
            "code": "staged",
            "description": "Stored a validated inactive batch.",
            "same_year_reupload": False,
        }
        event = {
            "id": 14,
            "attempted_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "deleted_at": None,
            "outcome": "staged",
            "survey_year": 2024,
            "batch_id": 8,
            "file_metadata": metadata,
            "error_count": 0,
            "warning_count": 1,
            "uploaded_by": "synthetic_admin",
            "action_by": "synthetic_admin",
            "source_provenance": None,
        }
        with patch(
            "services.import_history.import_validation_events.list_history",
            return_value=[event],
        ) as history:
            result = get_import_history(limit=25, connection_factory=FakeConnection)
        history.assert_called_once()
        self.assertEqual(history.call_args.kwargs["limit"], 25)
        self.assertEqual(result[0]["outcome_label"], "Batch stored")
        self.assertEqual(result[0]["total_records"], 8)
        self.assertEqual(result[0]["action_by"], "synthetic_admin")
        self.assertNotIn("file_metadata", result[0])
        self.assertNotIn("source_provenance", result[0])

    def test_csv_export_contains_aggregate_counts_and_escapes_formula_text(self):
        records = {
            name: {"bytes": 10, "sha256": "a" * 64, "records": index * 10}
            for index, name in enumerate(EXCEL_FILES, start=1)
        }
        records["_result"] = {
            "code": "staged",
            "description": "Stored a separate inactive batch; no existing data changed.",
            "same_year_reupload": True,
        }
        event = {
            "id": 12,
            "attempted_at": datetime(2026, 11, 21, 3, 30, tzinfo=timezone.utc),
            "outcome": "staged",
            "survey_year": 2024,
            "batch_id": 5,
            "file_metadata": records,
            "error_count": 0,
            "warning_count": 2,
            "uploaded_by": "=unsafe spreadsheet name",
            "action_by": "=unsafe spreadsheet name",
            "deleted_at": None,
            "source_provenance": None,
        }
        with patch(
            "services.import_history.import_validation_events.list_history",
            return_value=[event],
        ):
            exported = export_import_history(connection_factory=FakeConnection)
        rows = list(csv.reader(StringIO(exported)))
        self.assertEqual(len(rows), 2)
        result = dict(zip(rows[0], rows[1]))
        self.assertEqual(result["Uploaded by"], "'=unsafe spreadsheet name")
        self.assertEqual(result["Action by"], "'=unsafe spreadsheet name")
        self.assertEqual(result["Uploaded at"], result["Action at"])
        self.assertIn("separate inactive batch", result["Description"])
        self.assertEqual(result["Same-year re-upload"], "Yes")
        self.assertEqual(result["Successfully imported records"], "100")
        self.assertEqual(result["Duplicate or skipped records"], "0")
        self.assertEqual(result["Rejected records"], "0")
        self.assertEqual(result["Records containing validation errors"], "0")
        self.assertEqual(result["Warning types"], "2")
        self.assertNotIn("sha256", exported)
        self.assertNotIn("a" * 64, exported)

    def test_duplicate_bundle_history_reports_all_known_rows_as_skipped(self):
        metadata = {name: {"records": 3} for name in EXCEL_FILES}
        metadata["_result"] = {
            "code": "duplicate_bundle",
            "description": "Rejected an exact duplicate dataset; nothing was stored.",
            "same_year_reupload": False,
        }
        event = {
            "id": 13,
            "attempted_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "outcome": "rejected",
            "survey_year": 2024,
            "batch_id": None,
            "file_metadata": metadata,
            "error_count": 0,
            "warning_count": 0,
            "uploaded_by": "synthetic_admin",
            "action_by": "synthetic_admin",
            "deleted_at": None,
            "source_provenance": None,
        }
        with patch(
            "services.import_history.import_validation_events.list_history",
            return_value=[event],
        ):
            exported = export_import_history(connection_factory=FakeConnection)
        rows = list(csv.reader(StringIO(exported)))
        result = dict(zip(rows[0], rows[1]))
        self.assertEqual(result["Successfully imported records"], "0")
        self.assertEqual(result["Duplicate or skipped records"], "12")
        self.assertEqual(result["Rejected records"], "0")
        self.assertEqual(result["Same-year re-upload"], "No")


class ImportRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": token_urlsafe(48),
            "IMPORT_REFERENCE_DIR": None,
        })
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
        self.active_dataset = patch("routes.main.active_dataset_exists", return_value=True)
        self.active_dataset_mock = self.active_dataset.start()
        self.addCleanup(self.active_dataset.stop)
        self.stored_years = patch("routes.main.stored_survey_year_counts", return_value={})
        self.stored_years_mock = self.stored_years.start()
        self.addCleanup(self.stored_years.stop)
        self.import_history = patch(
            "routes.main.get_import_history_directory",
            return_value={
                "items": [], "matching_total": 0, "total": 0,
                "years": [], "limit": 25,
            },
        )
        self.import_history_mock = self.import_history.start()
        self.addCleanup(self.import_history.stop)

    def test_admin_can_open_and_staff_cannot_open_import_page(self):
        response = self.client.get("/administration/import")
        self.assertEqual(response.status_code, 200)
        for name in EXCEL_FILES:
            self.assertIn(f"{name}.csv".encode(), response.data)
        for filename in REFERENCE_FILES.values():
            self.assertNotIn(filename.encode(), response.data)
        self.assertIn(b'name="survey_year"', response.data)
        self.assertIn(b'<input id="survey-year"', response.data)
        self.assertIn(b'type="number"', response.data)
        self.assertIn(b'list="survey-year-options"', response.data)
        self.assertEqual(response.data.count(b"Maximum file size: 64 MB."), 4)
        self.assertIn(b'data-required-filename="household.csv"', response.data)
        self.assertIn(
            b'data-required-filename="section_l02_to_l41.csv"', response.data,
        )
        self.assertEqual(response.data.count(b'data-max-bytes="67108864"'), 4)
        self.assertIn(b"Each CSV must be 64 MB or smaller", response.data)
        self.assertIn(b'id="import-upload-form"', response.data)
        self.assertIn(b'id="import-processing-overlay"', response.data)
        self.assertIn(b"Data import in progress", response.data)
        self.assertIn(b"Import history", response.data)
        self.assertIn(b"Download full history", response.data)
        self.assertIn(b"No import activity yet", response.data)
        self.assertIn(b"Preview and validate files", response.data)
        self.assertNotIn(b"Confirm and store bundle", response.data)
        self.assertIn(b'<option value="2022">2022</option>', response.data)
        self.assertIn(b'<option value="2024">2024</option>', response.data)
        self.assertIn(b'<option value="2026">2026</option>', response.data)
        self.assertNotIn(b'<option value="2023">', response.data)
        script = (Path(__file__).resolve().parents[1]
                  / "static" / "js" / "data-import.js").read_text(encoding="utf-8")
        self.assertIn('form?.getAttribute("action")', script)
        self.assertIn("fetch(previewEndpoint", script)
        self.assertNotIn("fetch(form.action", script)
        self.role = "lgu_staff"
        self.assertEqual(self.client.get("/administration/import").status_code, 403)

    def test_later_current_year_can_be_entered_without_removing_established_choices(self):
        with patch("routes.main.latest_survey_year", return_value=2029):
            response = self.client.get("/administration/import")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'max="2029"', response.data)
        self.assertIn(b"enter a later official survey year up to 2029", response.data)
        for year in (2022, 2024, 2026):
            self.assertIn(f'<option value="{year}">{year}</option>'.encode(), response.data)

    def test_import_history_is_visible_in_its_own_page_section(self):
        event = {
            "id": 14,
            "uploaded_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "uploaded_by": "synthetic_admin",
            "action_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "action_by": "synthetic_admin",
            "outcome": "staged",
            "outcome_label": "Batch stored",
            "description": "Stored the validated files as a new inactive dataset batch.",
            "same_year_reupload": False,
            "survey_year": 2024,
            "batch_id": 8,
            "total_records": 19304,
            "error_count": 0,
            "warning_count": 1,
        }
        self.import_history_mock.return_value = {
            "items": [event], "matching_total": 1, "total": 1,
            "years": [2024], "limit": 25, "per_page": 25,
            "page": 1, "total_pages": 1, "first_item": 1, "last_item": 1,
            "has_previous": False, "has_next": False,
        }
        response = self.client.get("/administration/import")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="import-history-panel"', response.data)
        self.assertIn(b"Batch stored", response.data)
        self.assertIn(b"synthetic_admin", response.data)
        self.assertIn(b"19,304", response.data)
        self.assertIn(b"Stored the validated files", response.data)
        self.assertIn("Showing 1–1 of 1".encode(), response.data)
        self.assertIn(b"Page 1 of 1", response.data)
        self.import_history_mock.assert_called_with(
            parse_import_history_filters(MultiDict()), limit=25,
        )

    def test_import_history_pagination_preserves_filters_and_sort(self):
        event = {
            "id": 14,
            "uploaded_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "uploaded_by": "synthetic_admin",
            "action_at": datetime(2026, 11, 21, tzinfo=timezone.utc),
            "action_by": "synthetic_admin",
            "outcome": "staged",
            "outcome_label": "Batch stored",
            "description": "Stored a validated inactive dataset batch.",
            "same_year_reupload": False,
            "survey_year": 2024,
            "batch_id": 8,
            "total_records": 40,
            "error_count": 0,
            "warning_count": 0,
        }
        self.import_history_mock.return_value = {
            "items": [dict(event, id=index) for index in range(26, 51)],
            "matching_total": 80, "total": 100, "years": [2024, 2022],
            "limit": 25, "per_page": 25, "page": 2, "total_pages": 4,
            "first_item": 26, "last_item": 50,
            "has_previous": True, "has_next": True,
        }
        query = MultiDict((
            ("history_q", "admin"), ("history_outcome", "staged"),
            ("history_year", "2024"), ("history_sort", "oldest"),
            ("history_page", "2"),
        ))
        response = self.client.get("/administration/import", query_string=query)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Showing 26–50 of 80".encode(), response.data)
        self.assertIn(b"Page 2 of 4", response.data)
        text = response.get_data(as_text=True)
        page_links = re.findall(
            r'<a href="([^"]+)" rel="(?:prev|next)">', text,
        )
        self.assertEqual(len(page_links), 2)
        pages = set()
        for link in page_links:
            parameters = parse_qs(urlparse(unescape(link)).query)
            self.assertEqual(parameters["history_q"], ["admin"])
            self.assertEqual(parameters["history_outcome"], ["staged"])
            self.assertEqual(parameters["history_year"], ["2024"])
            self.assertEqual(parameters["history_sort"], ["oldest"])
            pages.add(parameters["history_page"][0])
        self.assertEqual(pages, {"1", "3"})
        self.import_history_mock.assert_called_with(
            parse_import_history_filters(query), limit=25,
        )

    def test_import_history_rejects_invalid_page_values(self):
        for value in ("0", "-1", "text"):
            with self.subTest(value=value):
                response = self.client.get(
                    "/administration/import", query_string={"history_page": value},
                )
                self.assertEqual(response.status_code, 400)

    def test_stored_batches_filter_by_year_and_preparation_status(self):
        batches = [
            {"id": 1, "survey_year": 2022, "is_active": True,
             "designated_run_id": 7, "run_count": 1, "households": 10},
            {"id": 2, "survey_year": 2024, "is_active": False,
             "designated_run_id": None, "run_count": 0, "households": 12},
        ]
        with patch("routes.main.list_import_batches", return_value=batches):
            response = self.client.get(
                "/administration/import/batches?year=2024&state=awaiting",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Batch 2", response.data)
        self.assertNotIn(b"Batch 1</h3>", response.data)
        self.assertIn(b"1 of 2 stored batches match", response.data)
        self.assertEqual(self.client.get(
            "/administration/import/batches?state=unknown",
        ).status_code, 400)

    def test_first_import_page_explains_baseline_setup(self):
        self.active_dataset_mock.return_value = False
        response = self.client.get("/administration/import")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No dataset is active yet", response.data)
        self.assertIn(b"activating it as the baseline dataset", response.data)

    def test_empty_admin_dashboard_leads_to_first_import(self):
        self.active_dataset_mock.return_value = False
        with patch("routes.main.get_active_dashboard_summary", return_value=None):
            response = self.client.get("/administration")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active dataset yet", response.data)
        self.assertIn(b"Go to Data Import", response.data)

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
                "csrf_token": "synthetic-csrf", "survey_year": "2021",
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"four-digit survey year from 2022", response.data)
            validate.assert_not_called()
        with patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None}) as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2023",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(validate.call_args.args[2], 2023)
        with patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None}) as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2024",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"All four files passed validation", response.data)
        validate.assert_called_once()

    def test_ajax_preview_shows_filenames_counts_and_confirmation(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {
            "passed": True,
            "datasets": {
                name: {"records": index * 10}
                for index, name in enumerate(EXCEL_FILES, start=1)
            },
            "errors": [],
            "warnings": [{
                "source": "section_l11_to_l14",
                "code": "identical_child_death_rows",
                "count": 2,
            }],
        }
        with (
            patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None,
                "preview_digest": "a" * 64,
            }),
            patch("routes.main.create_import_preview_token", return_value="signed-preview"),
        ):
            response = self.client.post(
                "/administration/import",
                data={"csrf_token": "synthetic-csrf", "survey_year": "2024"},
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        preview = response.get_json()["preview_html"]
        self.assertIn("household.csv", preview)
        self.assertIn("section_l11_to_l14.csv", preview)
        self.assertIn(">10<", preview)
        self.assertIn(">40<", preview)
        self.assertIn("Some child death records are identical.", preview)
        self.assertIn(">2</b>", preview)
        self.assertIn('name="preview_token" value="signed-preview"', preview)
        self.assertIn("Confirm and store bundle", preview)
        self.assertNotIn("Survey year", preview)

    def test_ajax_file_error_returns_the_specific_server_message(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with patch("routes.main.validate_admin_upload", return_value={
            "report": None,
            "error": "Use the exact filename household.csv for Households.",
        }):
            response = self.client.post(
                "/administration/import",
                data={"csrf_token": "synthetic-csrf", "survey_year": "2024"},
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "application/json")
        preview = response.get_json()["preview_html"]
        self.assertIn("Use the exact filename household.csv", preview)
        self.assertNotIn("file preview is unavailable", preview)

    def test_ajax_missing_column_error_is_rendered_in_the_preview(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {
            "passed": False,
            "datasets": {name: {"records": 2} for name in EXCEL_FILES},
            "errors": [{
                "source": "section_l02_to_l41",
                "code": "missing_defined_columns",
                "count": 1,
                "columns": ["barangay"],
            }],
            "warnings": [{
                "source": "section_l_to_members",
                "code": "missing_related_records",
                "count": 51068,
            }],
        }
        with patch("routes.main.validate_admin_upload", return_value={
            "report": report, "error": None,
        }):
            response = self.client.post(
                "/administration/import",
                data={"csrf_token": "synthetic-csrf", "survey_year": "2024"},
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        self.assertEqual(response.status_code, 400)
        preview = response.get_json()["preview_html"]
        self.assertIn("Missing required column:", preview)
        self.assertIn("<code>barangay</code>", preview)
        self.assertIn("Blocking issues", preview)
        self.assertIn("Data coverage note", preview)
        self.assertIn("This does not block import", preview)
        self.assertNotIn("Warnings to review", preview)

    def test_store_action_is_rejected_without_a_passing_preview(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with patch("routes.main.validate_admin_upload") as validate:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "survey_year": "2024",
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Preview and validate the selected files", response.data)
        validate.assert_not_called()

    def test_store_action_requires_confirmation_and_reports_inactive_batch(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {
            "passed": True,
            "datasets": {name: {"records": 10} for name in EXCEL_FILES},
            "errors": [], "warnings": [],
        }
        with (
            patch("routes.main.verify_import_preview_token", return_value="a" * 64),
            patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None, "batch_id": 8,
            }) as validate,
        ):
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "survey_year": "2024",
                "preview_token": "signed-preview",
            })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stored the previewed files as inactive batch 8", response.data)
        self.assertIn(b"Import results", response.data)
        self.assertIn(b"Successfully imported", response.data)
        self.assertIn(b"<dd>40</dd>", response.data)
        self.assertEqual(response.data.count(b"<dd>0</dd>"), 3)
        self.assertEqual(validate.call_args.args[2], 2024)
        self.assertTrue(validate.call_args.kwargs["store_inactive"])
        self.assertTrue(validate.call_args.kwargs["authorization_confirmed"])
        self.assertFalse(validate.call_args.kwargs["same_year_confirmed"])
        self.assertEqual(validate.call_args.kwargs["expected_preview_digest"], "a" * 64)

    def test_admin_can_download_aggregate_import_history(self):
        with patch("routes.main.export_import_history", return_value="Attempt ID\r\n1\r\n"):
            response = self.client.get("/administration/import/history.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn(
            'attachment; filename="tanglaw-buhay-import-history.csv"',
            response.headers["Content-Disposition"],
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.data.startswith("\ufeff".encode("utf-8")))
        self.assertIn(b"Attempt ID", response.data)
        self.role = "lgu_staff"
        self.assertEqual(
            self.client.get("/administration/import/history.csv").status_code, 403,
        )

    def test_existing_year_warns_and_passes_explicit_reupload_confirmation(self):
        self.stored_years_mock.return_value = {2024: 2}
        response = self.client.get("/administration/import")
        self.assertIn(b"2024 \xe2\x80\x94 2 stored batches", response.data)
        self.assertIn(b'id="duplicate-year-warning"', response.data)
        self.assertNotIn(b'name="confirm_same_year"', response.data)
        self.assertIn(b"does not replace, activate or change existing data", response.data)
        self.assertIn(b"Exact duplicate files will still be blocked", response.data)

        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {
            "passed": True,
            "datasets": {name: {"records": 1} for name in EXCEL_FILES},
            "errors": [], "warnings": [],
        }
        with (
            patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None, "preview_digest": "b" * 64,
            }),
            patch("routes.main.create_import_preview_token", return_value="signed-preview"),
        ):
            preview_response = self.client.post(
                "/administration/import",
                data={"csrf_token": "synthetic-csrf", "survey_year": "2024"},
                headers={"X-Requested-With": "fetch", "Accept": "application/json"},
            )
        preview = preview_response.get_json()["preview_html"]
        self.assertIn("This is a same-year re-upload", preview)
        self.assertIn("will not replace, activate or change", preview)
        self.assertIn("Confirm and store separate batch", preview)

        with (
            patch("routes.main.verify_import_preview_token", return_value="b" * 64),
            patch("routes.main.validate_admin_upload", return_value={
                "report": report, "error": None, "batch_id": 9,
            }) as validate,
        ):
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "confirm_same_year": "yes",
                "survey_year": "2024", "preview_token": "signed-preview",
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(validate.call_args.kwargs["same_year_confirmed"])

    def test_validation_service_failure_stays_on_import_page(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with patch(
            "routes.main.validate_admin_upload",
            side_effect=ImportValidationUnavailable("import_validation_unavailable"),
        ):
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "survey_year": "2024",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"temporarily unavailable", response.data)
        self.assertIn(b"Nothing was stored", response.data)
        self.assertNotIn(b"Request Unavailable", response.data)

    def test_storage_failure_explains_that_existing_data_was_unchanged(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with (
            patch("routes.main.verify_import_preview_token", return_value="a" * 64),
            patch(
                "routes.main.validate_admin_upload",
                side_effect=ImportValidationUnavailable("import_storage_unavailable"),
            ),
        ):
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "survey_year": "2024",
                "preview_token": "signed-preview",
            })
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Import could not be stored", response.data)
        self.assertIn(b"No existing data was changed", response.data)

    def test_duplicate_bundle_shows_error_without_success_notice(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        report = {"passed": True, "datasets": {}, "errors": [], "warnings": []}
        with (
            patch("routes.main.verify_import_preview_token", return_value="a" * 64),
            patch("routes.main.validate_admin_upload", return_value={
                "report": report,
                "error": "These exact CBMS source files are already stored. "
                         "Duplicate datasets cannot be imported.",
            }),
        ):
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "action": "store",
                "authorized_source": "yes", "survey_year": "2024",
                "preview_token": "signed-preview",
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Duplicate datasets cannot be imported.", response.data)
        self.assertNotIn(b"files passed validation", response.data)
        self.assertNotIn(b"import-result-badge-pass", response.data)
        self.assertNotIn(b"Correct the issues below", response.data)

    def test_oversized_request_gets_fixed_response_and_audit(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        self.app.config["IMPORT_MAX_CONTENT_LENGTH"] = 100
        with patch("routes.main.record_oversized_attempt") as audit:
            response = self.client.post("/administration/import", data={
                "csrf_token": "synthetic-csrf", "padding": "x" * 1024,
            })
        self.assertEqual(response.status_code, 413)
        self.assertIn(b"upload limit of about 268 MB", response.data)
        audit.assert_called_once_with(7)

    def test_admin_preparation_page_and_action_require_confirmation(self):
        batches = [{"id": 8, "survey_year": 2022, "is_active": False,
                    "households": 2, "run_count": 0, "designated_run_id": None,
                    "can_delete": True}]
        with patch("routes.main.list_import_batches", return_value=batches):
            response = self.client.get("/administration/import/batches")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Run and verify assessments", response.data)
            self.assertIn(b"Delete stored batch", response.data)
            self.assertIn(b"DELETE BATCH 8", response.data)
            self.assertIn(b'id="import-operation-overlay"', response.data)
            self.assertIn(b"Running and verifying assessments", response.data)
            self.assertIn(b"Deleting stored batch", response.data)
            self.assertIn(b"import-operations.js", response.data)
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

    def test_admin_can_permanently_delete_confirmed_inactive_batch(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        result = {"batch_id": 8, "survey_year": 2024,
                  "households": 2, "assessment_runs": 1}
        with patch("routes.main.delete_import_batch", return_value=result) as delete, \
             patch("routes.main.list_import_batches", return_value=[]):
            response = self.client.post(
                "/administration/import/batches/8/delete",
                data={"csrf_token": "synthetic-csrf",
                      "delete_confirmation": "DELETE BATCH 8"},
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        delete.assert_called_once_with(8, 7, "DELETE BATCH 8")
        self.assertIn(b"Batch 8 for survey year 2024 was permanently deleted", response.data)
        self.assertIn(b"deletion audit was retained", response.data)

    def test_batch_deletion_requires_exact_confirmation_and_admin_role(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with patch(
                "routes.main.delete_import_batch",
                side_effect=BatchDeletionError("invalid_deletion_request"),
        ) as delete:
            response = self.client.post(
                "/administration/import/batches/8/delete",
                data={"csrf_token": "synthetic-csrf", "delete_confirmation": "delete"},
            )
        self.assertEqual(response.status_code, 400)
        delete.assert_called_once_with(8, 7, "delete")
        self.role = "lgu_staff"
        response = self.client.post(
            "/administration/import/batches/8/delete",
            data={"csrf_token": "synthetic-csrf",
                  "delete_confirmation": "DELETE BATCH 8"},
        )
        self.assertEqual(response.status_code, 403)

    def test_batch_deletion_explains_retained_history_block(self):
        with self.client.session_transaction() as session:
            session["csrf_token"] = "synthetic-csrf"
        with patch("routes.main.delete_import_batch",
                   side_effect=BatchDeletionError("retained_history")), \
             patch("routes.main.list_import_batches", return_value=[]):
            response = self.client.post(
                "/administration/import/batches/8/delete",
                data={"csrf_token": "synthetic-csrf",
                      "delete_confirmation": "DELETE BATCH 8"},
                follow_redirects=True,
            )
        self.assertIn(b"retained activation, staff-review, or citizen-account history",
                      response.data)

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
            self.assertIn(b'id="import-operation-overlay"', response.data)
            self.assertIn(b"Activating dataset", response.data)
            self.assertIn(b"import-operations.js", response.data)
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
                    "action": "activate", "target_batch_id": 8,
                    "previous_batch_id": 1,
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

    def test_first_activation_uses_empty_previous_context(self):
        overview = {
            "active": None, "initial_setup": True,
            "candidates": [{"id": 8, "survey_year": 2022,
                            "households": 2, "run_id": 4}],
            "in_progress_reviews": 0, "citizen_accounts": 0,
            "open_document_requests": 0, "citizen_work_summary": "",
        }
        with patch("routes.main.activation_overview", return_value=overview), \
             patch("routes.main.activate_prepared_batch", return_value={
                 "action": "initial_activate", "target_batch_id": 8,
                 "previous_batch_id": None,
             }) as activate:
            response = self.client.get("/administration/import/activation")
            self.assertIn(b"Activate first dataset", response.data)
            with self.client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            response = self.client.post(
                "/administration/import/activation",
                data={
                    "csrf_token": "synthetic-csrf", "target_batch_id": "8",
                    "expected_active_batch_id": "", "expected_active_run_id": "",
                    "expected_target_run_id": "4", "confirm_switch": "yes",
                },
            )
        self.assertEqual(response.status_code, 302)
        activate.assert_called_once_with(
            8, 7, expected_active_batch_id=None,
            expected_active_run_id=None, expected_target_run_id=4,
        )

    def test_activation_page_blocks_switch_for_citizen_records(self):
        overview = {
            "active": {"id": 1, "survey_year": 2022, "households": 2, "run_id": 3},
            "candidates": [{"id": 8, "survey_year": 2022, "households": 2, "run_id": 4}],
            "in_progress_reviews": 0, "citizen_accounts": 2, "open_document_requests": 1,
            "citizen_work_summary": describe_citizen_work(2, 1),
        }
        with patch("routes.main.activation_overview", return_value=overview):
            response = self.client.get("/administration/import/activation")
            self.assertEqual(response.status_code, 200)
            text = re.sub(rb"<[^>]+>", b"", response.data)
            self.assertIn(b"Citizen accounts and document requests need attention", text)
            self.assertIn(b"2 citizen accounts that are active or waiting for activation "
                          b"and 1 unfinished document request", text)
            self.assertIn(b"deactivate the accounts and accept or cancel the requests", text)
            self.assertNotIn(b"Switch to Batch 8", response.data)
            self.assertIn(b"Resolve the items shown above", text)
            with self.client.session_transaction() as session:
                session["csrf_token"] = "synthetic-csrf"
            blocked = BatchActivationError(
                "citizen_work_open", account_count=1, request_count=2)
            with patch("routes.main.activate_prepared_batch", side_effect=blocked):
                response = self.client.post("/administration/import/activation", data={
                    "csrf_token": "synthetic-csrf", "target_batch_id": "8",
                    "expected_active_batch_id": "1", "expected_active_run_id": "3",
                    "expected_target_run_id": "4", "confirm_switch": "yes",
                })
            self.assertEqual(response.status_code, 409)
            self.assertIn(b"1 citizen account that is active or waiting for activation "
                          b"and 2 unfinished document requests", response.data)

    def test_citizen_work_wording_handles_each_case(self):
        self.assertEqual(describe_citizen_work(1, 0),
                         "1 citizen account that is active or waiting for activation")
        self.assertEqual(describe_citizen_work(0, 3), "3 unfinished document requests")
        self.assertEqual(describe_citizen_work(0, 0), "")

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
