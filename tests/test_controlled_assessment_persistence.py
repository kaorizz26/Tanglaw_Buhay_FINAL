"""Synthetic runner safeguards and failure paths; never open a database connection."""

from copy import deepcopy
import json
import unittest
from unittest.mock import MagicMock, patch

from scripts import controlled_assessment_persistence_2022 as runner
from scripts import controlled_cbms_import_2022 as guard
from services.assessments import build_unified_assessments
from services.development_data import development_sources


class ControlledPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.__exit__.return_value = False
        self.transaction = self.connection.transaction.return_value
        self.transaction.__exit__.return_value = False

    def test_check_is_read_only_and_never_calculates_or_executes(self):
        with patch.object(runner, "connect_verified", return_value=self.connection), \
                patch.object(runner, "file_hashes", return_value={}), \
                patch.object(runner, "preflight", return_value=({}, False, {})), \
                patch.object(runner, "execute_once") as execute, \
                patch.object(runner, "calculate_once") as calculate, \
                patch.object(runner, "emit") as emit:
            self.assertEqual(runner.main(["--check"]), 0)
        execute.assert_not_called()
        calculate.assert_not_called()
        self.connection.execute.assert_called_once_with(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        self.assertEqual(emit.call_args.args[0]["check"], "PASS")

    def test_explicit_mode_required_and_private_errors_suppressed(self):
        with patch.object(runner, "connect_verified") as connect, \
                patch.object(runner, "emit") as emit:
            self.assertEqual(runner.main([]), 1)
            connect.assert_not_called()
        self.assertEqual(emit.call_args.args[0]["safe_error_code"], "explicit_mode_required")
        with patch.object(runner, "file_hashes", side_effect=ValueError("SYNTHETIC_PRIVATE_VALUE")), \
                patch.object(runner, "emit") as emit:
            self.assertEqual(runner.main(["--execute"]), 1)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", json.dumps(emit.call_args.args[0]))

    def test_existing_pending_or_completed_pilot_run_stops_before_calculation(self):
        # Metadata parsing is independently tested by the loader; isolate repeat protection here.
        metadata = {"batch_id": 1, "survey_year": 2022, "municipality": "Pozorrubio"}
        for counts in ((1, 0), (1, 32), (0, 32)):
            with self.subTest(counts=counts), \
                    patch.object(runner, "verify_schema"), \
                    patch.object(runner, "_batch_metadata", return_value=metadata), \
                    patch.object(runner, "table_counts", return_value=runner.EXPECTED), \
                    patch.object(runner, "assessment_schema_present", return_value=True), \
                    patch.object(runner, "preservation_snapshot") as snapshot:
                self.connection.execute.return_value.fetchone.return_value = counts
                with self.assertRaisesRegex(runner.VerificationError, "existing_pilot_assessment_stop"):
                    runner.preflight(self.connection)
                snapshot.assert_not_called()

    def test_partial_migration_is_rejected_without_ddl(self):
        self.connection.execute.return_value.fetchone.return_value = (True, False)
        with self.assertRaisesRegex(runner.VerificationError, "partial_assessment_schema"):
            runner.assessment_schema_present(self.connection)
        self.assertEqual(self.connection.execute.call_count, 1)

    def test_atomic_migration_failure_rolls_back_and_never_retries(self):
        with patch.object(runner, "connect_verified", return_value=self.connection) as connect, \
                patch.object(runner, "assessment_schema_present", side_effect=[False, True]), \
                patch.object(runner, "preflight", return_value=({}, False, {})), \
                patch.object(runner, "file_hashes", return_value={}), \
                patch.object(runner, "calculate_once", side_effect=runner.VerificationError("synthetic_failure")), \
                patch.object(runner.repository, "create_run") as create:
            with self.assertRaisesRegex(runner.VerificationError, "synthetic_failure"):
                runner.execute_once({}, {})
        connect.assert_called_once()
        create.assert_not_called()
        self.assertIs(self.transaction.__exit__.call_args.args[0], runner.VerificationError)
        statements = [str(call.args[0]) for call in self.connection.execute.call_args_list]
        self.assertIn(runner.MIGRATION.read_text(encoding="utf-8"), statements)
        self.assertFalse(any("UPDATE public.staff_users" in value for value in statements))

    def test_source_change_prevents_migration_and_engine_execution(self):
        with patch.object(runner, "connect_verified", return_value=self.connection), \
                patch.object(runner, "assessment_schema_present", return_value=False), \
                patch.object(runner, "preflight", return_value=({}, False, {"changed": True})), \
                patch.object(runner, "calculate_once") as calculate:
            with self.assertRaisesRegex(runner.VerificationError, "source_or_staff_changed_before_execution"):
                runner.execute_once({}, {})
        calculate.assert_not_called()
        self.assertNotIn(runner.MIGRATION.read_text(encoding="utf-8"),
                         [str(call.args[0]) for call in self.connection.execute.call_args_list])

    def test_storage_failure_rolls_back_and_does_not_finalize_or_retry(self):
        with patch.object(runner, "connect_verified", return_value=self.connection) as connect, \
                patch.object(runner, "assessment_schema_present", return_value=True), \
                patch.object(runner, "preflight", return_value=({}, True, {})), \
                patch.object(runner, "file_hashes", return_value={}), \
                patch.object(runner, "calculate_once", return_value=({}, {})), \
                patch.object(runner, "calculation_provenance", return_value={"synthetic": True}), \
                patch.object(runner.repository, "create_run", return_value=7), \
                patch.object(runner.repository, "store_household_assessments",
                             side_effect=runner.VerificationError("synthetic_storage_failure")), \
                patch.object(runner.repository, "complete_run") as complete:
            with self.assertRaisesRegex(runner.VerificationError, "synthetic_storage_failure"):
                runner.execute_once({}, {})
        connect.assert_called_once()
        complete.assert_not_called()
        self.assertIs(self.transaction.__exit__.call_args.args[0], runner.VerificationError)

    def test_full_synthetic_findings_and_ranks_compared_without_recalculation(self):
        datasets, references = development_sources()
        original = build_unified_assessments(datasets, references, survey_year=2022)
        ordered = sorted(original.values(), key=lambda finding: (
            finding["mpi"]["vulnerability_rank"], finding["household_uuid"],
        ))
        metadata = {"batch_id": 1, "survey_year": 2022, "municipality": "Pozorrubio",
                    "status": "completed", "expected_household_count": len(original),
                    "calculation_version": runner.CALCULATION_VERSION}
        self.connection.execute.return_value.fetchone.return_value = (1, 1, len(original), 4 * len(original))
        with patch.object(runner.repository, "find_run", return_value=metadata), \
                patch.object(runner.repository, "iter_household_assessments", return_value=ordered):
            self.assertEqual(runner.verify_stored(self.connection, 7, original)["households"], 32)
        changed = deepcopy(ordered)
        changed[0]["programs"]["SLP"]["methodology_notes"] += ("SYNTHETIC_CHANGED_NOTE",)
        with patch.object(runner.repository, "find_run", return_value=metadata), \
                patch.object(runner.repository, "iter_household_assessments", return_value=changed):
            with self.assertRaisesRegex(runner.VerificationError, "stored_complete_findings_mismatch"):
                runner.verify_stored(self.connection, 7, original)

    def test_existing_connection_guard_refuses_wrong_remote_or_service_targets(self):
        for url in ("dbname=tanglaw_buhay_test host=localhost", "dbname=tanglaw_buhay_old host=localhost",
                    "dbname=tanglaw_buhay_dev host=example.invalid", "service=synthetic"):
            with self.subTest(url=url), patch.object(guard.Config, "DATABASE_URL", url), \
                    patch.object(guard.psycopg, "connect") as connect:
                with self.assertRaises(RuntimeError):
                    guard.connect_verified()
                connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
